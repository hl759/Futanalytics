"""SigmaDesk — API FastAPI (Etapa 0: camada de dados validada + leitura de vol).

Escopo desta etapa, por decisão do usuário: PROVAR que a camada de dados
funciona antes de construir o motor em cima. Por isso aqui já existe leitura
real de volatilidade (IV × RV × VRP), mas ainda não existe recomendação de
estrutura, sizing por estresse nem checklist A/B/C — isso é Etapa 1-4.

Três regras herdadas do FutAnalytics que não mudam:

1. NADA É GRAVADO SOZINHO. Trades, resultados e CVL só entram quando o usuário
   clica. O único dado escrito automaticamente é cache temporário com TTL e
   purga na inicialização — sem ele as cotas gratuitas estourariam.
2. FALHA é diferente de VAZIO (lição da v2.4.1). O painel mostra qual provedor
   foi pedido, qual foi usado e o erro exato de cada um. Nunca silêncio.
3. HONESTIDADE DE FONTE. Todo número carrega `source`: deribit / coinbase /
   kraken / demo. IV sintética nunca se passa por preço de mercado.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import db, provider, volread

APP_NAME = "SigmaDesk"
VERSION = "0.1.0-etapa0"
CODENAME = "camada de dados validada"

app = FastAPI(title=APP_NAME, version=VERSION)
db.init()

STATIC = Path(__file__).resolve().parent.parent / "static"


# ------------------------------------------------------------------ configurações
class Settings(BaseModel):
    # capital (decisão 2 do usuário: configurável, padrão editável)
    bankroll: float = 1000.0
    display_currency: str = "USD"

    # universo (decisão 4: SÓ BTC e ETH)
    currencies: str = "BTC,ETH"

    # risco (decisão 3: SÓ risco definido até haver CVL acumulado)
    defined_risk_only: bool = True
    naked_short_enabled: bool = False
    portfolio_min_grade: str = "B"
    max_positions: int = 4
    kelly_fraction: float = 0.25
    stake_cap_pct: float = 3.0
    max_vega_pct: float = 10.0          # teto de vega agregado (% do capital)
    max_net_short_premium_pct: float = 5.0

    # parâmetros de estresse (o sizing pergunta o pior destes três)
    stress_iv_bump: float = 3.0          # pontos de IV contra
    stress_rv_multiple: float = 2.0      # RV realizada = 2x a prevista
    stress_gap_sigma: float = 2.0        # gap adverso no subjacente

    # modelo (herdado do FutAnalytics: 25% modelo, 75% mercado)
    model_weight: float = 0.25
    use_calibration: bool = True
    max_spread_pct: float = 0.25
    min_oi: float = 0.5

    # horizonte preferido para o VRP
    target_dte: float = 30.0

    # cache (orçamento de chamadas do Render free)
    ttl_chain: int = 900                 # 15 min: superfície inteira em 1 chamada
    ttl_candles: int = 3600
    ttl_dvol: int = 3600
    candle_days: int = 400
    dvol_days: int = 400

    # provedor
    provider_force: str = ""             # "" = cadeia automática com fallback
    binance_enabled: bool = False        # geo-bloqueada em IP dos EUA (Render)


DEFAULTS = Settings()


def load_settings() -> Settings:
    vals = {}
    for name, default in DEFAULTS.model_dump().items():
        raw = db.get_setting(name)
        if raw is None:
            continue
        try:
            if isinstance(default, bool):
                vals[name] = str(raw).lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int) and not isinstance(default, bool):
                vals[name] = int(float(raw))
            elif isinstance(default, float):
                vals[name] = float(raw)
            else:
                vals[name] = str(raw)
        except (TypeError, ValueError):
            continue
    return Settings(**vals)


def _ccys(s: Settings) -> list[str]:
    out = [c.strip().upper() for c in (s.currencies or "BTC,ETH").split(",") if c.strip()]
    return [c for c in out if c in provider.CURRENCIES] or ["BTC"]


# ------------------------------------------------------------------ dados com cache
async def _candles(ccy: str, s: Settings, deadline: float | None = None):
    key = f"candles:{ccy}:{s.candle_days}:{s.provider_force}"
    hit = db.cache_get(key)
    if hit and hit.get("data"):
        return hit["data"], hit["source"], {**hit.get("debug", {}), "cached": True}
    data, source, debug = await provider.candles_with_fallback(
        ccy, s.candle_days, force=s.provider_force or None, deadline=deadline)
    db.cache_set(key, {"data": data, "source": source, "debug": debug}, s.ttl_candles)
    db.api_usage_inc(str(dt.date.today()), len(debug.get("asked", [])))
    return data, source, debug


async def _chain(ccy: str, s: Settings, deadline: float | None = None):
    key = f"chain:{ccy}"
    hit = db.cache_get(key)
    if hit and hit.get("data"):
        return hit["data"], hit["source"], {**hit.get("debug", {}), "cached": True}
    data, source, debug = await provider.chain_with_fallback(ccy, deadline=deadline)
    db.cache_set(key, {"data": data, "source": source, "debug": debug}, s.ttl_chain)
    db.api_usage_inc(str(dt.date.today()), len(debug.get("asked", [])))
    return data, source, debug


async def _dvol(ccy: str, s: Settings, deadline: float | None = None):
    key = f"dvol:{ccy}:{s.dvol_days}"
    hit = db.cache_get(key)
    if hit and hit.get("data"):
        return hit["data"], hit["source"], {**hit.get("debug", {}), "cached": True}
    data, source, debug = await provider.dvol_with_fallback(ccy, s.dvol_days,
                                                            deadline=deadline)
    db.cache_set(key, {"data": data, "source": source, "debug": debug}, s.ttl_dvol)
    db.api_usage_inc(str(dt.date.today()), 1)
    return data, source, debug


async def _read(ccy: str, s: Settings, deadline: float | None = None) -> dict:
    """Leitura completa de uma moeda: dados + superfície + VRP.

    As três camadas saem em PARALELO e compartilham um único deadline. Antes eram
    três `await` sequenciais: com provedor black-holando, candles gastava 3×20s,
    chain 20s, dvol 20s = 100s por moeda, e duas moedas em sequência = 200s por
    request. O painel não carregava. O cache continua valendo por camada.
    """
    (candles, c_src, c_dbg), (chain, o_src, o_dbg), (dvol, d_src, d_dbg) = await asyncio.gather(
        _candles(ccy, s, deadline), _chain(ccy, s, deadline), _dvol(ccy, s, deadline))
    t0 = time.monotonic()
    try:
        read = volread.read_surface(chain, candles, dvol, ccy)
    except Exception as e:                      # nunca derruba o painel
        read = {"ok": False, "ccy": ccy, "error": f"{type(e).__name__}: {e}"}
    read["sources"] = {"candles": c_src, "options": o_src, "dvol": d_src}
    read["debug"] = {"candles": c_dbg, "options": o_dbg, "dvol": d_dbg}
    read["compute_ms"] = round((time.monotonic() - t0) * 1000, 1)
    read["is_demo"] = "demo" in (c_src, o_src, d_src)
    read["params"] = volread.PARAMS
    read["as_of"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return read


# ------------------------------------------------------------------ endpoints
@app.get("/api/version")
def version():
    return {"app": APP_NAME, "version": VERSION, "codename": CODENAME,
            "lineage": "FutAnalytics v2.4.4 -> SigmaDesk (migração de arquitetura preditiva)",
            "etapa": 0,
            "escopo_etapa": "camada de dados validada contra APIs reais + leitura de vol"}


@app.get("/api/model-info")
def model_info():
    s = load_settings()
    return {
        "mercado": "volatilidade de criptoativos (opções BTC/ETH, Deribit)",
        "sinal_mestre": "VRP = IV implícita − RV prevista",
        "motor": ("dois horizontes EWMA (meia-vida 58d/11d, peso curto 25%) + "
                  "estimadores de amplitude (Parkinson/Garman-Klass/Yang-Zhang) + "
                  "separação de salto por bipower variation"),
        "parametros": volread.PARAMS,
        "heranca": {
            "DECAY_LONG/DECAY_SHORT/SHORT_WEIGHT": "PARAMS do FutAnalytics (model.py)",
            "RANGE_WEIGHT/RANGE_MIN_BARS": "XG_WEIGHT/XG_MIN_COVERAGE",
            "REGIME_PRIOR_STRENGTH": "LEAGUE_PRIOR_STRENGTH",
            "model_weight 0.25": "blend_markets(model, odds, 0.25)",
        },
        "anualizacao": "sqrt(365) — cripto não tem dia útil",
        "universo": _ccys(s),
        "risco_definido_apenas": s.defined_risk_only,
        "venda_descoberta": s.naked_short_enabled,
        "pendente": ["estruturas e precificação (Etapa 3)",
                     "checklist A/B/C (Etapa 3)",
                     "cross-section ridge (Etapa 4)",
                     "backtest QLIKE/CVL (Etapa 5)"],
    }


@app.get("/api/settings")
def get_settings():
    return load_settings().model_dump()


class SettingsIn(BaseModel):
    bankroll: float | None = None
    display_currency: str | None = None
    currencies: str | None = None
    defined_risk_only: bool | None = None
    naked_short_enabled: bool | None = None
    portfolio_min_grade: str | None = None
    max_positions: int | None = None
    kelly_fraction: float | None = None
    stake_cap_pct: float | None = None
    max_vega_pct: float | None = None
    max_net_short_premium_pct: float | None = None
    stress_iv_bump: float | None = None
    stress_rv_multiple: float | None = None
    stress_gap_sigma: float | None = None
    model_weight: float | None = None
    use_calibration: bool | None = None
    max_spread_pct: float | None = None
    min_oi: float | None = None
    target_dte: float | None = None
    ttl_chain: int | None = None
    ttl_candles: int | None = None
    ttl_dvol: int | None = None
    candle_days: int | None = None
    dvol_days: int | None = None
    provider_force: str | None = None
    binance_enabled: bool | None = None


# Limites de sanidade. BUG PEGO NO SMOKE TEST DA ETAPA 0: POST /api/settings
# aceitava `bankroll: -50` e gravava. Como stake_pct = notional/bankroll*100, um
# capital negativo produziu sizing de −3108% — e um capital ZERO daria divisão por
# zero no /api/portfolio. Configuração de risco sem limite não é configuração, é
# armadilha: o único campo que separa uma estrutura dimensionada de uma aposta
# descuidada é justamente este número.
#
# (mínimo, máximo) — None significa "sem limite naquele lado".
BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "bankroll": (1.0, None),                    # > 0: entra em TODO cálculo de tamanho
    "kelly_fraction": (0.0, 1.0),               # fração de Kelly, nunca acima de full
    "stake_cap_pct": (0.0, 100.0),
    "max_vega_pct": (0.0, 100.0),
    "max_net_short_premium_pct": (0.0, 100.0),
    "max_spread_pct": (0.0, 100.0),
    "min_oi": (0.0, None),
    "model_weight": (0.0, 1.0),                 # 0=tudo mercado, 1=tudo modelo
    "target_dte": (1.0, 730.0),
    "stress_iv_bump": (0.0, 50.0),
    "stress_rv_multiple": (1.0, 10.0),          # <1 não é estresse, é alívio
    "stress_gap_sigma": (0.0, 10.0),
    "max_positions": (1, 50),
    "ttl_chain": (0, 86400),
    "ttl_candles": (0, 86400),
    "ttl_dvol": (0, 86400),
    "candle_days": (30, 3000),
    "dvol_days": (30, 3000),
}
# mudar qualquer uma destas invalida as leituras em cache
_INVALIDATES_CACHE = {"currencies", "candle_days", "dvol_days", "provider_force",
                      "binance_enabled", "min_oi", "max_spread_pct", "target_dte"}


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    """Atualiza o perfil. ALL-OR-NOTHING.

    Por que não aplicar só os campos válidos: isto é um PERFIL DE RISCO, não uma
    lista de preferências. O dimensionamento de uma estrutura depende da
    COMBINAÇÃO (capital × teto de vega × fração de Kelly × DTE alvo). Aplicar
    metade de um payload com erro produziria um perfil que o usuário nunca
    revisou e nunca aprovou — e ele descobriria isso olhando o tamanho de uma
    posição, não a tela de configuração. Rejeitar tudo com 422 e dizer exatamente
    o que está errado é mais barato que desfazer depois.

    BUG PEGO NO SMOKE TEST DA ETAPA 0: este handler não validava nada.
    `bankroll: -50` era gravado; como stake_pct = notional/bankroll*100, o sizing
    do trade seguinte saiu −3108%. Com bankroll 0 haveria divisão por zero no
    /api/portfolio.
    """
    incoming = body.model_dump(exclude_none=True)
    rejected: dict[str, str] = {}

    for k, v in incoming.items():
        if k == "currencies" and v:
            v = ",".join(c.strip().upper() for c in v.split(",") if c.strip())
            parts = [c for c in v.split(",") if c]
            bad = [c for c in parts if c not in provider.CURRENCIES]
            if not parts:
                rejected[k] = "universo vazio"
            elif bad:
                rejected[k] = (f"fora do escopo: {','.join(bad)} "
                               f"(v1 é só {','.join(provider.CURRENCIES)})")
            incoming[k] = v
            continue
        if k == "display_currency" and v not in ("USD", "BRL"):
            rejected[k] = f"moeda de exibição inválida: {v} (USD ou BRL)"
            continue
        if k == "portfolio_min_grade" and v not in ("A", "B", "C"):
            rejected[k] = f"grade inválido: {v} (A, B ou C)"
            continue
        if k == "provider_force" and v not in ("", "auto", "deribit", "coinbase",
                                               "kraken", "demo"):
            rejected[k] = f"provider desconhecido: {v}"
            continue
        b = BOUNDS.get(k)
        if b and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi = b
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                rejected[k] = (f"{v} fora do intervalo "
                               f"[{lo if lo is not None else '-inf'}, "
                               f"{hi if hi is not None else '+inf'}]")
                continue
        incoming[k] = v

    if rejected:                                  # nada é gravado
        raise HTTPException(422, {
            "erro": "configuração rejeitada; nada foi alterado",
            "rejected": rejected,
            "aceitos_que_nao_foram_aplicados": sorted(set(incoming) - set(rejected)),
            "settings": load_settings().model_dump(),
        })

    saved = {}
    for k, v in incoming.items():
        db.set_setting(k, str(v))
        saved[k] = v

    # invalida o cache de leitura SE algo que afeta os dados mudou.
    # (o código anterior tinha aqui um `for ... : pass` — invalidação nenhuma
    # acontecia, então trocar o universo servia a superfície antiga até o TTL.)
    cleared = 0
    if set(saved) & _INVALIDATES_CACHE:
        # só os prefixos que _candles/_chain/_dvol realmente escrevem
        for prefix in ("chain:", "candles:", "dvol:"):
            cleared += db.cache_clear(prefix)

    return {"ok": True, "saved": saved, "rejected": {},
            "cache_cleared": cleared,
            "settings": load_settings().model_dump()}


@app.get("/api/test-provider")
async def test_provider(which: str = "all", ccy: str = "BTC"):
    ccy = ccy.upper() if ccy.upper() in provider.CURRENCIES else "BTC"
    try:
        return await provider.status(which, ccy)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@app.get("/api/radar")
async def radar(ccy: str | None = None):
    """Leitura de volatilidade do universo configurado."""
    s = load_settings()
    targets = [ccy.upper()] if ccy else _ccys(s)
    targets = [t for t in targets if t in provider.CURRENCIES] or ["BTC"]
    # deadline ÚNICO para o request inteiro, não por chamada: é o que garante
    # teto de latência mesmo quando várias camadas degradam ao mesmo tempo.
    deadline = time.monotonic() + provider.DEADLINE_S
    results = await asyncio.gather(*[_read(t, s, deadline) for t in targets],
                                   return_exceptions=True)
    out, errors = [], {}
    for t, r in zip(targets, results):
        if isinstance(r, provider.ProviderError):
            errors[t] = str(r)
        elif isinstance(r, BaseException):
            errors[t] = f"{type(r).__name__}: {r}"
        else:
            out.append(r)
    return {
        "as_of": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "settings": {"bankroll": s.bankroll, "target_dte": s.target_dte,
                     "defined_risk_only": s.defined_risk_only,
                     "model_weight": s.model_weight},
        "reads": out,
        "errors": errors,
        "etapa": 0,
        "aviso": ("Etapa 0: leitura de volatilidade validada contra dados reais. "
                  "Recomendação de estrutura, sizing por estresse e checklist A/B/C "
                  "vêm nas Etapas 1-4. Nada aqui é recomendação de investimento."),
    }


@app.get("/api/surface")
async def surface(ccy: str = "BTC", expiry: str | None = None, liquid_only: bool = True):
    """Superfície crua: toda a cadeia ou um vencimento. Para conferir na corretora."""
    s = load_settings()
    ccy = ccy.upper() if ccy.upper() in provider.CURRENCIES else "BTC"
    chain, src, dbg = await _chain(ccy, s, time.monotonic() + provider.DEADLINE_S)
    rows = chain
    if expiry:
        rows = [o for o in rows if o["expiry"] == expiry]
    if liquid_only:
        rows = [o for o in rows if o["liquid"]] or rows
    return {"ccy": ccy, "source": src, "debug": dbg, "n": len(rows),
            "expiries": sorted({o["expiry"] for o in chain}),
            "options": rows[:600]}


@app.get("/api/history")
async def history(ccy: str = "BTC", days: int = 180):
    """Séries para gráfico: DVOL (IV) × RV realizada × preço."""
    s = load_settings()
    ccy = ccy.upper() if ccy.upper() in provider.CURRENCIES else "BTC"
    deadline = time.monotonic() + provider.DEADLINE_S
    (candles, c_src, _), (dvol, d_src, _) = await asyncio.gather(
        _candles(ccy, s, deadline), _dvol(ccy, s, deadline))
    days = max(20, min(days, s.candle_days))
    cut = candles[-days:]
    rv_series = []
    for i in range(30, len(candles) + 1):
        r = volread.realized_vol(candles[max(0, i - 90):i])
        if r.get("ok"):
            rv_series.append({"ts": candles[i - 1]["ts"], "rv30": r["rv"]})
    return {
        "ccy": ccy, "days": days,
        "sources": {"candles": c_src, "dvol": d_src},
        "candles": cut,
        "dvol": [d for d in dvol if d["ts"] >= cut[0]["ts"]] if cut else dvol,
        "rv_series": rv_series[-days:],
    }


# ------------------------------------------------------------------ diário de trades
class TradeIn(BaseModel):
    ccy: str = "BTC"
    structure: str
    direction: str = "short_vol"
    legs: list[dict] = []
    entry_iv: float | None = None
    premium: float | None = None
    notional: float | None = None
    contracts: float | None = None
    vega: float | None = None
    delta: float | None = None
    max_loss_stress: float | None = None
    prob_edge: float | None = None
    ev: float | None = None
    grade: str | None = None
    check_score: float | None = None
    expiry: str | None = None
    opened_at: str | None = None
    notes: str = ""


@app.post("/api/trades")
def add_trade(t: TradeIn):
    s = load_settings()
    d = t.model_dump()
    d["ccy"] = d["ccy"].upper()
    if d["ccy"] not in provider.CURRENCIES:
        raise HTTPException(400, f"moeda fora do radar (só {', '.join(provider.CURRENCIES)})")
    if d.get("bankroll_at_entry") is None:
        d["bankroll_at_entry"] = s.bankroll
    if d.get("stake_pct") is None and d.get("notional") and s.bankroll:
        d["stake_pct"] = round(abs(d["notional"]) / s.bankroll * 100, 3)
    tid = db.add_trade(d)
    return {"ok": True, "id": tid, "trade": db.get_trade(tid)}


class SettleIn(BaseModel):
    status: str = "closed"
    pnl: float = 0.0
    notes: str | None = None


@app.post("/api/trades/{trade_id}/settle")
def settle_trade(trade_id: int, body: SettleIn):
    t = db.get_trade(trade_id)
    if not t:
        raise HTTPException(404, "trade não encontrado")
    if body.status not in ("open", "closed", "expired", "cancelled"):
        raise HTTPException(400, "status inválido")
    fields = {"status": body.status, "pnl": body.pnl}
    if body.notes is not None:
        fields["notes"] = body.notes
    db.update_trade(trade_id, fields)
    return {"ok": True, "trade": db.get_trade(trade_id)}


class ClosingIn(BaseModel):
    closing_iv: float


@app.post("/api/trades/{trade_id}/closing-iv")
def set_closing_iv(trade_id: int, body: ClosingIn):
    """CVL — o análogo exato do CLV do FutAnalytics.

    Para quem VENDE vol, CVL positivo significa que a IV caiu entre a entrada e
    o fechamento: você vendeu mais caro do que o mercado passou a precificar.
    É o melhor preditor de edge de longo prazo disponível — melhor que o P&L do
    trade individual, que depende de um caminho de preço que ninguém controla.
    """
    t = db.get_trade(trade_id)
    if not t:
        raise HTTPException(404, "trade não encontrado")
    entry = t.get("entry_iv")
    cvl = None
    if entry:
        sign = -1.0 if (t.get("direction") or "").startswith("long") else 1.0
        cvl = round(sign * (entry - body.closing_iv), 3)
    db.update_trade(trade_id, {"closing_iv": body.closing_iv, "cvl": cvl})
    return {"ok": True, "cvl": cvl, "trade": db.get_trade(trade_id)}


@app.get("/api/trades")
def list_trades(status: str | None = None):
    return {"trades": db.list_trades(status), "stats": db.stats()}


@app.delete("/api/trades/{trade_id}")
def delete_trade(trade_id: int):
    ok = db.delete_trade(trade_id)
    if not ok:
        raise HTTPException(404, "trade não encontrado")
    return {"ok": True}


@app.get("/api/portfolio")
def portfolio():
    """Posições abertas agregadas. Etapa 0: agregação simples.

    Na Etapa 4 entra aqui a correlação MEDIDA via cross-section ridge — hoje a
    agregação é aritmética, o que SUPERESTIMA a diversificação. Está marcado de
    propósito: melhor avisar que o número é otimista do que fingir precisão.
    """
    open_trades = db.list_trades("open")
    s = load_settings()
    vega = sum(abs(t.get("vega") or 0) for t in open_trades)
    notional = sum(abs(t.get("notional") or 0) for t in open_trades)
    stress = sum(abs(t.get("max_loss_stress") or 0) for t in open_trades)
    per_ccy: dict[str, int] = {}
    for t in open_trades:
        per_ccy[t["ccy"]] = per_ccy.get(t["ccy"], 0) + 1
    return {
        "open_positions": len(open_trades),
        "max_positions": s.max_positions,
        "vega_total": round(vega, 4),
        "vega_pct_bankroll": round(vega / s.bankroll * 100, 3) if s.bankroll else None,
        "vega_teto_pct": s.max_vega_pct,
        "vega_acima_do_teto": bool(s.bankroll and vega / s.bankroll * 100 > s.max_vega_pct),
        "notional_total": round(notional, 2),
        "stress_loss_total": round(stress, 2),
        "stress_pct_bankroll": round(stress / s.bankroll * 100, 2) if s.bankroll else None,
        "per_ccy": per_ccy,
        "aviso": ("agregação aritmética: sem correlação medida ainda (Etapa 4). "
                  "BTC e ETH compartilham fator de vol comum — tratar como "
                  "NÃO diversificado até o cross-section entrar."),
    }


# ------------------------------------------------------------------ backup
@app.get("/api/backup")
def export_backup():
    return db.export_all()


@app.post("/api/backup")
def import_backup(payload: dict):
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload inválido")
    return {"ok": True, **db.import_all(payload)}


@app.get("/api/labels")
def labels():
    return {
        "currencies": list(provider.CURRENCIES),
        "providers": provider.PROVIDER_LABELS,
        "structures_defined_risk": [
            "iron_condor", "put_spread", "call_spread", "calendar_spread",
            "diagonal_spread", "butterfly",
        ],
        "structures_naked": ["short_strangle", "short_straddle", "short_put", "short_call"],
        "directions": ["short_vol", "long_vol", "neutral_carry", "skew"],
        "grades": ["A", "B", "C"],
        "vrp_bands": [
            {"band": "muito rico", "min": 6.0, "action": "tamanho cheio"},
            {"band": "saudável", "min": 3.0, "action": "tamanho padrão"},
            {"band": "magro", "min": 1.0, "action": "reduz, risco definido"},
            {"band": "marginal", "min": 0.0, "action": "mínimo ou fique de fora"},
            {"band": "NEGATIVO", "min": None, "action": "não venda prêmio"},
        ],
    }


@app.get("/api/health")
def health():
    return {"ok": True, "app": APP_NAME, "version": VERSION,
            "api_calls_today": db.api_usage_today(str(dt.date.today()))}


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    f = STATIC / "index.html"
    if f.exists():
        return FileResponse(f)
    return JSONResponse({"app": APP_NAME, "version": VERSION,
                         "hint": "static/index.html ausente"})
