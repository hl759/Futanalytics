"""Camada de dados do SigmaDesk — Etapa 0.

Herdeira direta de `provider.py` do FutAnalytics (mesma filosofia: provedor
principal + fallbacks grátis sem chave + modo demo + pacing + cache). A cadeia
esportiva era `fd -> openliga -> espn -> demo`; a financeira é

    deribit --falha--> coinbase --falha--> kraken --falha--> demo

------------------------------------------------------------------------------
O QUE A ETAPA 0 DESCOBRIU (validado contra as APIs reais em 26/set/2026)
------------------------------------------------------------------------------
1. `public/get_book_summary_by_currency?currency=BTC&kind=option` devolve a
   CADEIA INTEIRA de opções com `mark_iv`, `bid_price`, `ask_price`,
   `open_interest`, `volume`, `underlying_price` — tudo em UMA chamada pública
   sem chave. É o análogo exato do `fixtures.csv` do football-data.co.uk que
   sustentava as odds grátis. Resposta grande (~56 páginas de JSON p/ BTC).

2. `public/get_volatility_index_data` exige **`currency`**, NÃO `index_name`.
   Passar `index_name` devolve erro JSON-RPC -32602 `{"reason":"value
   required","param":"currency"}`. (O plano original errava isso.)

3. DVOL tem **piso duro em 2024-01-01**. Pedindo `start_timestamp` de 2019 o
   primeiro ponto continua sendo 1704067200000 = 01/jan/2024. Portanto o
   backtest de VRP no nível de índice vive em ~2,7 anos de diário. Raso, mas
   real e grátis.

4. **`api.binance.com` está geo-bloqueada**: devolve
   `{"code":0,"msg":"Service unavailable from a restricted location according
   to 'b. Eligibility'"}`. O Render free hospeda em Ohio/EUA — Binance como
   fallback MORRERIA em produção sem aviso. Por isso a cadeia usa Coinbase
   Exchange e Kraken, que responderam normalmente. Binance fica disponível
   apenas como fonte opcional explícita (`provider="binance"`), nunca no
   caminho automático.

5. `get_tradingview_chart_data` da própria Deribit serve OHLCV diário desde
   jan/2024 — mesmo provedor, zero risco geográfico novo.

6. Cross-check de integridade: close BTC 25/set/2026 na Coinbase = 84.003,06;
   `estimated_delivery_price` da Deribit = 84.003,27. Duas fontes
   independentes concordam em 21 centavos.

7. Formatos que MORDIAM se não fossem documentados aqui:
   * Coinbase devolve `[time, LOW, HIGH, open, close, volume]` — LOW vem ANTES
     de HIGH, ordem diferente da Binance. E vem em ordem DESCENDENTE.
   * Deribit DVOL devolve `[ts, open, high, low, close]` ASCENDENTE, sem volume.
   * `get_historical_volatility` devolve `[ts, valor]` e REPETE o primeiro ponto.
   * Opções da Deribit são INVERSAS: `quote_currency == "BTC"` (preço em BTC,
     não em USD). `mark_iv` vem em PORCENTAGEM (18.69, não 0.1869).
   * `bid_price` pode ser `null` em strike ilíquido — é o caso real, não bug.
     Isso é exatamente o que o item "liquidez da ponta" do checklist existe
     para pegar.

------------------------------------------------------------------------------
ORÇAMENTO DE CHAMADAS (Render free)
------------------------------------------------------------------------------
Deribit pública não-autenticada é limitada POR IP, sistema de créditos:
500 créditos/chamada padrão, pool 50.000, refill 10.000 créditos/s.
Exceção cara: `public/get_instruments` = 10.000 créditos (1 req/s) — por isso
fica em cache de 24h e NÃO é usada no caminho quente; `get_book_summary_*` já
traz strike/expiry embutidos no nome do instrumento.

A documentação pública diverge sobre o teto real para não-autenticado (há fonte
citando 20 req/min). O pacing abaixo é conservador de propósito e configurável.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import time

import httpx

DR_BASE = "https://www.deribit.com/api/v2"
CB_BASE = "https://api.exchange.coinbase.com"
KR_BASE = "https://api.kraken.com/0/public"
BN_BASE = "https://api.binance.com/api/v3"          # opcional: geo-bloqueada em US
BN_MIRROR = "https://data-api.binance.vision/api/v3"  # espelho público de dados

UA = {"User-Agent": "Mozilla/5.0 (compatible; SigmaDesk/0.1; +local research)"}

# ---------------------------------------------------------------------------
# Orçamento de tempo — restrição do RENDER FREE, não preferência estética.
#
# O pior caso antigo era: candles tenta deribit→coinbase→kraken (3×20s = 60s),
# chain 20s, dvol 20s = 100s POR MOEDA, e as duas moedas eram sequenciais =
# 200s por request. Aqui dentro as conexões falham rápido (TLS EOF em ~0,1s),
# então media 3,5s e parecia ótimo. Num IP de datacenter onde o provedor
# BLACK-HOLA os pacotes em vez de rejeitá-los, cada tentativa consome o timeout
# inteiro — e o painel simplesmente não carrega.
#
# APIs de dados de mercado respondem em <1s quando estão saudáveis. 20s de
# timeout não é tolerância, é latência garantida para o usuário.
TIMEOUT = 8.0              # por chamada individual
DEADLINE_S = 25.0          # teto GLOBAL por request; passou disso, o que
                           # faltar vira demo com aviso
BREAKER_FAILS = 2          # falhas consecutivas para abrir o disjuntor
BREAKER_COOLDOWN = 180.0   # segundos pulando o provedor depois de abrir

# v4 da decisão do usuário: SÓ BTC e ETH. Nada de cauda longa com ponta fina.
CURRENCIES = ("BTC", "ETH")

COINBASE_PRODUCTS = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD"}
DERIBIT_INDEX = {"BTC": "btc_usd", "ETH": "eth_usd"}
DERIBIT_PERP = {"BTC": "BTC-PERPETUAL", "ETH": "ETH-PERPETUAL"}

# segundos entre chamadas ao MESMO host (conservador; medido na Etapa 0)
MIN_INTERVAL = {"deribit": 0.6, "coinbase": 0.25, "kraken": 0.7, "binance": 0.25}
_last_call: dict[str, float] = {}

MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# Deribit liquida opções às 08:00 UTC do dia do vencimento
DELIVERY_HOUR_UTC = 8

# Um strike é negociável se tem dois lados e spread não é absurdo.
MAX_SPREAD_PCT = 0.25      # (ask-bid)/mid acima disso = ponta morta
MIN_OI = 0.5               # open interest mínimo para entrar no radar


class ProviderError(Exception):
    """Falha de provedor com mensagem legível para aparecer no painel."""


# ------------------------------------------------------------------ utilidades
# ------------------------------------------------------------------ disjuntor
# Sem isto, CADA carga de página depois do TTL vencer paga de novo o timeout
# inteiro de um provedor que já sabemos estar morto. Com 3 camadas × 2 moedas,
# são até 6 penalidades repetidas por visita — num free tier que dorme após
# 15 min, o cold start viraria minutos.
_breaker: dict[str, dict] = {}


def breaker_state(host: str) -> str:
    b = _breaker.get(host)
    if not b or not b.get("opened_at"):
        return "closed"
    if time.monotonic() - b["opened_at"] > BREAKER_COOLDOWN:
        return "half-open"          # deixa tentar uma vez
    return "open"


def breaker_skip(host: str) -> bool:
    return breaker_state(host) == "open"


def breaker_note(host: str, ok: bool, err: str = "") -> None:
    b = _breaker.setdefault(host, {"fails": 0, "opened_at": None, "last_error": ""})
    if ok:
        b.update(fails=0, opened_at=None, last_error="")
        return
    b["fails"] += 1
    b["last_error"] = err[:200]
    if b["fails"] >= BREAKER_FAILS:
        b["opened_at"] = time.monotonic()


def _skip_reason(host: str) -> str:
    b = _breaker.get(host, {})
    retry = (round(BREAKER_COOLDOWN - (time.monotonic() - b.get("opened_at", 0)), 1)
             if b.get("opened_at") else 0)
    return (f"pulado: disjuntor aberto após {b.get('fails', 0)} falhas "
            f"(última: {str(b.get('last_error', '?'))[:70]}; "
            f"nova tentativa em {max(retry, 0):.0f}s)")


def breaker_report() -> dict:
    """Estado dos disjuntores para o diagnóstico do painel."""
    out = {}
    for host, b in _breaker.items():
        st = breaker_state(host)
        out[host] = {
            "state": st, "fails": b["fails"], "last_error": b["last_error"],
            "retry_in_s": (round(BREAKER_COOLDOWN - (time.monotonic() - b["opened_at"]), 1)
                           if st == "open" and b.get("opened_at") else 0),
        }
    return out


async def _guarded(coro, host: str, deadline: float | None):
    """Roda uma chamada respeitando timeout individual E deadline global.

    Devolve (resultado, erro). Nunca levanta — o painel precisa distinguir
    FALHA de VAZIO (lição da v2.4.1 do FutAnalytics).
    """
    budget = TIMEOUT
    if deadline is not None:
        budget = min(budget, deadline - time.monotonic())
    if budget <= 0.3:
        return None, f"deadline do request esgotado antes de tentar {host}"
    try:
        return await asyncio.wait_for(coro, timeout=budget), None
    except asyncio.TimeoutError:
        return None, f"{host} não respondeu em {budget:.1f}s (timeout)"
    except ProviderError as e:
        return None, str(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


async def _pace(host: str) -> None:
    """Ritma chamadas ao mesmo host (herdeiro de `_fd_pace` do FutAnalytics)."""
    interval = MIN_INTERVAL.get(host, 0.3)
    now = time.monotonic()
    last = _last_call.get(host, 0.0)
    wait = interval - (now - last)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call[host] = time.monotonic()


def _f(x) -> float | None:
    """Float tolerante: None, string vazia e NaN viram None."""
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def _now_ms() -> int:
    return int(time.time() * 1000)


def parse_instrument(name: str) -> dict | None:
    """Decompõe o nome de instrumento da Deribit.

    Formatos reais:
        BTC-28SEP26-88000-P          (inversa, cotada em BTC)
        BTC-USDC-26SEP25-84000-C     (linear, cotada em USDC)
        BTC-PERPETUAL                (perpétuo)
        BTC-26SEP26                  (futuro com vencimento)
    """
    if not name or "-" not in name:
        return None
    parts = name.split("-")
    ccy = parts[0]
    if ccy not in CURRENCIES:
        return None
    if len(parts) == 2:
        return {"ccy": ccy, "kind": "perpetual" if parts[1] == "PERPETUAL" else "future",
                "expiry": None, "strike": None, "option": None, "settlement": "coin"}
    if len(parts) == 5 and parts[1] == "USDC":       # opção linear
        _c, _usdc, exp, strike, opt = parts
        settlement = "USDC"
    elif len(parts) == 4:                            # opção inversa
        _c, exp, strike, opt = parts
        settlement = "coin"
    else:
        return None
    if opt not in ("C", "P"):
        return None
    d = parse_expiry(exp)
    k = _f(strike)
    if d is None or k is None:
        return None
    return {"ccy": ccy, "kind": "option", "expiry": d, "strike": k,
            "option": opt, "settlement": settlement}


def parse_expiry(code: str) -> dt.date | None:
    """'28SEP26' -> date(2026,9,28)  ·  '9OCT26' -> date(2026,10,9)

    BUG PEGO POR DADO REAL NA ETAPA 0: a Deribit NÃO zero-à-esquerda o dia.
    Vencimentos de dia 1 a 9 vêm como '9OCT26', '3OCT26', '1AUG26'. Um parser
    que assume 2 dígitos fixos perde silenciosamente ~9% dos vencimentos — e
    como são justamente os mais curtos (diários/semanais), destruiria a parte
    mais líquida da superfície sem levantar erro nenhum.

    Por isso a leitura é ANCORA DA DIREITA: os 5 últimos caracteres são sempre
    MÊS(3 letras) + ANO(2 dígitos); o que sobra à esquerda é o dia.
    """
    if not code or len(code) < 6:
        return None
    mon = MONTHS.get(code[-5:-2].upper())
    if mon is None:
        return None
    try:
        day = int(code[:-5])
        year = int(code[-2:]) + 2000
    except ValueError:
        return None
    try:
        return dt.date(year, mon, day)
    except ValueError:      # dia impossível, ex.: 31FEB26
        return None


def delivery_dt(expiry: dt.date) -> dt.datetime:
    return dt.datetime(expiry.year, expiry.month, expiry.day,
                       DELIVERY_HOUR_UTC, tzinfo=dt.timezone.utc)


def days_to_expiry(expiry: dt.date, now: dt.datetime | None = None) -> float:
    """DTE fracionário — cripto não tem dia útil, o tempo é contínuo."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return max(0.0, (delivery_dt(expiry) - now).total_seconds() / 86400.0)


# ------------------------------------------------------------------ normalização
def _candle(ts, o, h, l, c, v=None) -> dict:
    ts = int(ts)
    return {"ts": ts if ts > 10**11 else ts * 1000,
            "o": _f(o), "h": _f(h), "l": _f(l), "c": _f(c), "v": _f(v) or 0.0}


def _option_row(r: dict, now: dt.datetime) -> dict | None:
    """Linha crua do book summary -> opção normalizada."""
    meta = parse_instrument(r.get("instrument_name", ""))
    if not meta or meta["kind"] != "option":
        return None
    bid, ask = _f(r.get("bid_price")), _f(r.get("ask_price"))
    mid = _f(r.get("mid_price"))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2
    spread_pct = None
    if bid is not None and ask is not None and mid and mid > 0:
        spread_pct = (ask - bid) / mid
    iv = _f(r.get("mark_iv"))
    oi = _f(r.get("open_interest")) or 0.0
    dte = days_to_expiry(meta["expiry"], now)
    liquid = (bid is not None and ask is not None and mid is not None and mid > 0
              and (spread_pct or 9) <= MAX_SPREAD_PCT and oi >= MIN_OI)
    return {
        "instrument": r["instrument_name"],
        "ccy": meta["ccy"],
        "settlement": meta["settlement"],
        "expiry": meta["expiry"].isoformat(),
        "strike": meta["strike"],
        "kind": meta["option"],
        "dte": round(dte, 3),
        "mark_iv": round(iv, 3) if iv is not None else None,   # em % (18.69)
        "bid": bid, "ask": ask, "mid": mid,
        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
        "mark_price": _f(r.get("mark_price")),
        "last": _f(r.get("last")),
        "oi": oi,
        "volume": _f(r.get("volume")) or 0.0,
        "volume_usd": _f(r.get("volume_usd")) or 0.0,
        "underlying_price": _f(r.get("underlying_price")),
        "underlying_index": r.get("underlying_index"),
        "liquid": liquid,
    }


# ------------------------------------------------------------------ DERIBIT
async def dr_rpc(client: httpx.AsyncClient, method: str, params: dict | None = None):
    """JSON-RPC sobre GET. Lança ProviderError com o motivo real do JSON-RPC."""
    await _pace("deribit")
    url = f"{DR_BASE}/{method}"
    try:
        r = await client.get(url, params=params or {}, timeout=TIMEOUT, headers=UA)
        r.raise_for_status()
        payload = r.json()
    except httpx.HTTPStatusError as e:
        raise ProviderError(f"Deribit HTTP {e.response.status_code} em {method}") from e
    except (httpx.HTTPError, ValueError) as e:
        raise ProviderError(f"Deribit inacessível em {method}: {e}") from e
    if "error" in payload and payload["error"]:
        err = payload["error"]
        raise ProviderError(
            f"Deribit {method}: [{err.get('code')}] {err.get('message')} "
            f"{(err.get('data') or '')}")
    return payload.get("result")


async def dr_option_chain(ccy: str = "BTC") -> list[dict]:
    """CADEIA INTEIRA de opções de uma moeda em uma chamada (descoberta 1)."""
    if ccy not in CURRENCIES:
        raise ProviderError(f"moeda {ccy} fora do radar (só {', '.join(CURRENCIES)})")
    async with httpx.AsyncClient() as client:
        rows = await dr_rpc(client, "public/get_book_summary_by_currency",
                            {"currency": ccy, "kind": "option"})
    if not isinstance(rows, list):
        raise ProviderError(f"Deribit devolveu formato inesperado para cadeia {ccy}")
    now = dt.datetime.now(dt.timezone.utc)
    out = [o for o in (_option_row(r, now) for r in rows) if o]
    if not out:
        raise ProviderError(f"cadeia {ccy} vazia (0 opções parseáveis de {len(rows)} linhas)")
    out.sort(key=lambda o: (o["expiry"], o["strike"], o["kind"]))
    return out


async def dr_dvol(ccy: str = "BTC", resolution: int = 86400,
                  days: int = 1000) -> list[dict]:
    """DVOL diário (o VIX de cripto). Piso duro: 2024-01-01 (descoberta 3).

    ATENÇÃO: o parâmetro é `currency`, não `index_name` (descoberta 2).
    """
    end = _now_ms()
    start = end - days * 86400 * 1000
    async with httpx.AsyncClient() as client:
        res = await dr_rpc(client, "public/get_volatility_index_data", {
            "currency": ccy, "resolution": resolution,
            "start_timestamp": start, "end_timestamp": end,
        })
    data = (res or {}).get("data") or []
    out = []
    for row in data:
        if len(row) < 5:
            continue
        out.append({"ts": int(row[0]), "o": _f(row[1]), "h": _f(row[2]),
                    "l": _f(row[3]), "c": _f(row[4])})
    return out


async def dr_hv(ccy: str = "BTC") -> list[dict]:
    """Volatilidade histórica publicada pela Deribit (janela curta, ~semanas).

    Não é a fonte principal de RV — serve de conferência cruzada contra a RV
    que o SigmaDesk calcula a partir do OHLCV. Devolve pontos repetidos:
    deduplicados aqui.
    """
    async with httpx.AsyncClient() as client:
        rows = await dr_rpc(client, "public/get_historical_volatility", {"currency": ccy})
    seen, out = set(), []
    for row in rows or []:
        if len(row) < 2:
            continue
        ts = int(row[0])
        if ts in seen:
            continue
        seen.add(ts)
        out.append({"ts": ts, "hv": _f(row[1])})
    return out


async def dr_ohlcv(ccy: str = "BTC", resolution: str = "1D",
                   days: int = 400) -> list[dict]:
    """OHLCV da própria Deribit (descoberta 5) — mesmo provedor, sem risco geo."""
    end = _now_ms()
    start = end - days * 86400 * 1000
    async with httpx.AsyncClient() as client:
        res = await dr_rpc(client, "public/get_tradingview_chart_data", {
            "instrument_name": DERIBIT_INDEX.get(ccy, f"{ccy.lower()}_usd"),
            "resolution": resolution,
            "start_timestamp": start, "end_timestamp": end,
        })
    res = res or {}
    ticks = res.get("ticks") or []
    o, h, l, c = (res.get("open") or [], res.get("high") or [],
                  res.get("low") or [], res.get("close") or [])
    v = res.get("volume") or []
    n = min(len(ticks), len(o), len(h), len(l), len(c))
    out = [_candle(ticks[i], o[i], h[i], l[i], c[i], v[i] if i < len(v) else 0.0)
           for i in range(n)]
    out.sort(key=lambda x: x["ts"])
    return out


async def dr_funding(ccy: str = "BTC", days: int = 120) -> list[dict]:
    """Funding do perpétuo (segmento B: carry).

    NÃO validado empiricamente na Etapa 0 — implementado de forma defensiva:
    tolera as formas conhecidas da resposta e devolve [] em vez de explodir.
    """
    end = _now_ms()
    start = end - days * 86400 * 1000
    try:
        async with httpx.AsyncClient() as client:
            res = await dr_rpc(client, "public/get_funding_chart_data", {
                "instrument_name": DERIBIT_PERP.get(ccy, f"{ccy}-PERPETUAL"),
                "resolution": "3600",
                "start_timestamp": start, "end_timestamp": end,
            })
    except ProviderError:
        return []
    data = (res or {}).get("data") or []
    out = []
    for row in data:
        if len(row) < 3:
            continue
        out.append({"ts": int(row[0]), "interest_8h": _f(row[1]),
                    "funding_8h": _f(row[2])})
    return out


# ------------------------------------------------------------------ COINBASE
async def cb_candles(ccy: str = "BTC", granularity: int = 86400,
                     days: int = 300) -> list[dict]:
    """OHLCV da Coinbase Exchange — sem chave, sem bloqueio geográfico (descoberta 4).

    Duas armadilhas documentadas: a ordem é [time, LOW, HIGH, open, close, vol]
    e a resposta vem DESCENDENTE. Normalizo para ascendente.
    A API limita ~300 velas por chamada: para mais, pagina por start/end.
    """
    product = COINBASE_PRODUCTS.get(ccy)
    if not product:
        raise ProviderError(f"Coinbase sem produto para {ccy}")
    out: list[dict] = []
    end = dt.datetime.now(dt.timezone.utc)
    # pagina para trás: 300 velas por vez
    per_page = min(300, days)
    cursor = end
    remaining = days
    async with httpx.AsyncClient() as client:
        while remaining > 0:
            await _pace("coinbase")
            start = cursor - dt.timedelta(seconds=per_page * granularity)
            params = {"granularity": granularity,
                      "start": start.isoformat(), "end": cursor.isoformat()}
            try:
                r = await client.get(f"{CB_BASE}/products/{product}/candles",
                                     params=params, timeout=TIMEOUT, headers=UA)
                r.raise_for_status()
                rows = r.json()
            except httpx.HTTPStatusError as e:
                raise ProviderError(f"Coinbase HTTP {e.response.status_code}") from e
            except (httpx.HTTPError, ValueError) as e:
                raise ProviderError(f"Coinbase inacessível: {e}") from e
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if len(row) < 6:
                    continue
                t, lo, hi, op, cl, vol = row[:6]
                out.append(_candle(t, op, hi, lo, cl, vol))
            cursor = start
            remaining -= per_page
            if len(rows) < 2:
                break
    # dedup + ordem ascendente
    dedup = {c["ts"]: c for c in out}
    res = sorted(dedup.values(), key=lambda c: c["ts"])
    if not res:
        raise ProviderError(f"Coinbase devolveu 0 velas para {product}")
    return res


# ------------------------------------------------------------------ KRAKEN
async def kraken_ohlcv(ccy: str = "BTC", interval: int = 1440,
                       days: int = 300) -> list[dict]:
    """Fallback 2: Kraken público, sem chave, sem bloqueio geo."""
    pair = KRAKEN_PAIRS.get(ccy)
    if not pair:
        raise ProviderError(f"Kraken sem par para {ccy}")
    since = _now_ms() - days * 86400 * 1000
    await _pace("kraken")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{KR_BASE}/OHLC",
                                 params={"pair": pair, "interval": interval, "since": since // 1000},
                                 timeout=TIMEOUT, headers=UA)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPStatusError as e:
            raise ProviderError(f"Kraken HTTP {e.response.status_code}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"Kraken inacessível: {e}") from e
    if payload.get("error"):
        raise ProviderError(f"Kraken: {payload['error']}")
    result = payload.get("result") or {}
    key = next((k for k in result if k != "last"), None)
    rows = result.get(key) or []
    out = []
    for row in rows:
        if len(row) < 7:
            continue
        t, op, hi, lo, cl = row[0], row[1], row[2], row[3], row[4]
        out.append(_candle(t, op, hi, lo, cl, row[6] if len(row) > 6 else 0.0))
    out.sort(key=lambda c: c["ts"])
    if not out:
        raise ProviderError("Kraken devolveu 0 velas")
    return out


# ------------------------------------------------------------------ BINANCE (opcional)
async def bn_klines(ccy: str = "BTC", interval: str = "1d", days: int = 300,
                    mirror: bool = True) -> list[dict]:
    """NÃO entra na cadeia automática (descoberta 4: geo-bloqueada em US).

    Existe como fonte explícita para quem roda fora de região restrita, e tenta
    o espelho público de dados antes do host principal.
    """
    symbol = f"{ccy}USDT"
    start = _now_ms() - days * 86400 * 1000
    bases = [BN_MIRROR, BN_BASE] if mirror else [BN_BASE]
    last_err = None
    async with httpx.AsyncClient() as client:
        for base in bases:
            await _pace("binance")
            try:
                r = await client.get(f"{base}/klines",
                                     params={"symbol": symbol, "interval": interval,
                                             "startTime": start, "limit": min(days, 1000)},
                                     timeout=TIMEOUT, headers=UA)
                r.raise_for_status()
                rows = r.json()
                if isinstance(rows, dict) and rows.get("msg"):
                    raise ProviderError(f"Binance: {rows['msg'][:90]}")
            except (httpx.HTTPError, ValueError, ProviderError) as e:
                last_err = e
                continue
            out = []
            for row in rows or []:
                if len(row) < 8:
                    continue
                out.append(_candle(row[0], row[1], row[2], row[3], row[4], row[5]))
            out.sort(key=lambda c: c["ts"])
            if out:
                return out
            last_err = ProviderError("Binance devolveu 0 velas")
    raise ProviderError(f"Binance indisponível ({last_err})")


# ------------------------------------------------------------------ DEMO
def demo_seed(ccy: str, salt: int = 0) -> int:
    """Seed DETERMINÍSTICA derivada da moeda.

    BUG PEGO NA ETAPA 0: o código usava `hash(ccy) & 0xFFFF`. Em CPython o
    `hash()` de `str` é salgado por processo (PYTHONHASHSEED), então o "modo demo
    determinístico" gerava uma HISTÓRIA DIFERENTE A CADA RESTART do servidor —
    medido: o close de 30 dias atrás saía 78.673 num processo e 92.535 noutro.
    Isso quebra reprodutibilidade, invalida cache, faz o painel mostrar números
    que mudam sem o mercado ter mudado, e impede comparar teste entre processos.
    Só apareceu ao rodar a mesma função em dois processos separados.
    """
    return (sum((i + 1) * ord(ch) for i, ch in enumerate(ccy.upper())) + salt) & 0xFFFF


def demo_candles(ccy: str = "BTC", days: int = 400, seed: int | None = None) -> list[dict]:
    """OHLCV sintético determinístico, ancorado no último valor REAL capturado.

    Âncoras reais de 26/set/2026: BTC 84.003 (Coinbase e Deribit concordando em
    21 centavos). Serve para desenvolver o painel e para o app nunca ficar sem
    dados — sempre rotulado `demo` na tela, como era no FutAnalytics.
    """
    rnd = _Lcg(seed if seed is not None else demo_seed(ccy))
    spot = 84003.06 if ccy == "BTC" else 3200.0
    # ann_vol é a vol do GERADOR; a RV medida sai um pouco diferente porque os
    # estimadores de amplitude (Parkinson/GK/YZ) capturam o high-low, que aqui é
    # alargado artificialmente. Com 0,42 o blend media 47,5% contra uma RV REAL de
    # 39,18% (26/set/2026) — o demo fingia um mercado 8 pontos mais volátil.
    # Varrido empiricamente com a seed determinística: 0,363 -> RV 39,1%.
    # ETH não foi capturado: 0,55 é estimativa, não medição.
    ann_vol = 0.363 if ccy == "BTC" else 0.55
    day_vol = ann_vol / math.sqrt(365)
    now = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    price = spot * math.exp(-0.15 * (days / 365))   # parte de trás no tempo
    for i in range(days):
        d = now - dt.timedelta(days=days - 1 - i)
        drift = 0.15 / 365
        shock = day_vol * rnd.normal()
        o = price
        c = o * math.exp(drift + shock)
        hi = max(o, c) * (1 + abs(rnd.normal()) * day_vol * 0.55)
        lo = min(o, c) * (1 - abs(rnd.normal()) * day_vol * 0.55)
        vol = spot * 0.02 * (0.4 + rnd.uniform())
        out.append({"ts": int(d.timestamp() * 1000), "o": round(o, 2), "h": round(hi, 2),
                    "l": round(lo, 2), "c": round(c, 2), "v": round(vol, 4)})
        price = c
    # Ancora a série INTEIRA no último close real (BTC 84.003 / ETH 3.200, ambos
    # de 26/set/2026). Escala uniforme preserva a coerência OHLC; a versão
    # anterior escalava só a última vela e deixava `o` fora da escala, o que
    # podia produzir open > high. Bug pego pela validação da Etapa 0.
    if out and out[-1]["c"]:
        k = spot / out[-1]["c"]
        out = [{"ts": c["ts"], "o": c["o"] * k, "h": c["h"] * k,
                "l": c["l"] * k, "c": c["c"] * k, "v": c["v"]} for c in out]
    for c in out:
        c["o"] = round(c["o"], 2); c["c"] = round(c["c"], 2)
        # o arredondamento pode violar o invariante em 1 centavo — repara
        c["h"] = round(max(c["h"], c["o"], c["c"]), 2)
        c["l"] = round(min(c["l"], c["o"], c["c"]), 2)
    return out


def deribit_expiry_code(d: dt.date) -> str:
    """date(2026,10,9) -> '9OCT26'   (SEM zero à esquerda no dia)

    A Deribit não zero-pads o dia: 9OCT26, 3OCT26, 1AUG26. `strftime('%d%b%y')`
    produz '09OCT26' — que o parser antigo aceitava e a exchange nunca emite.
    O demo gerava nomes que SÓ o demo sabia ler, então o bug do dia de 1 dígito
    passou ileso por qualquer teste que usasse dado sintético. Foi preciso dado
    real para pegá-lo. Regra que fica: o modo demo emite a MESMA convenião da
    exchange, senão ele não valida nada — só valida a si mesmo.
    """
    return f"{d.day}{dt.datetime(d.year, d.month, 1).strftime('%b').upper()}{d.year % 100:02d}"


def demo_chain(ccy: str = "BTC", seed: int | None = None) -> list[dict]:
    """Superfície sintética com sorriso, skew e estrutura a termo realistas.

    ÂNCORAS REAIS capturadas em 26/set/2026 (BTC):
      spot 84.003,06 (Coinbase ≈ estimated_delivery_price da Deribit, Δ 21¢)
      DVOL oficial 34,88 · IV 30d interpolada da cadeia 35,38 (Δ 0,50 pt)
      RV 30d realizada 39,18 · VRP = −3,8 pts (NEGATIVO: IV abaixo da RV)
      estrutura a termo real: 1,5d→18,69 · 12,5d→32,27 · 19,5d→33,22 ·
                              33,5d→35,78 · 61,5d→49,22
      forward por vencimento: 84.022 / 84.376 / 85.052 / 86.084 / 88.318

    ETH NÃO foi capturado — as âncoras de ETH abaixo são estimativa, não medição,
    e estão marcadas como tal. Não trate número de ETH do demo como observado.
    """
    rnd = _Lcg(seed if seed is not None else demo_seed(ccy, salt=7))
    spot = 84003.06 if ccy == "BTC" else 3200.0
    # term(30d) = atm * (1 + 0.18*exp(-30/20)) = atm * 1.0408; com atm=34.0 isso
    # devolve 35,4% em 30 dias — a IV interpolada REAL de 26/set/2026 (35,38%).
    atm = 34.0 if ccy == "BTC" else 42.0
    carry = 0.0012 if ccy == "BTC" else 0.0008      # por dia, contango observado
    expiries = []
    now = dt.datetime.now(dt.timezone.utc)
    for dte in (2, 7, 14, 30, 60, 90, 180):
        expiries.append((now + dt.timedelta(days=dte)).date())
    out = []
    for exp in expiries:
        dte = max(1, (exp - now.date()).days)
        fwd = spot * math.exp(carry * dte)
        # estrutura a termo de vol: curto mais alto (prêmio de evento) + mean reversion
        term = atm * (1.0 + 0.18 * math.exp(-dte / 20.0))
        strikes = _strike_ladder(spot, ccy, n=13)
        for k in strikes:
            moneyness = math.log(k / fwd)
            # skew: put mais caro que call; sorriso convexo
            skew = -6.5 * moneyness
            smile = 26.0 * moneyness * moneyness * math.sqrt(max(dte, 1) / 30.0)
            iv = max(8.0, term + skew + smile + rnd.normal() * 0.6)
            for opt in ("C", "P"):
                iv_o = iv + (0.4 if opt == "P" else -0.4)
                oi = max(0.0, rnd.uniform() * 400 * math.exp(-abs(moneyness) * 3))
                deep = abs(moneyness) > 0.55
                bid = None if deep or oi < MIN_OI else 0.01
                ask = None if deep else 0.012
                out.append({
                    "instrument": f"{ccy}-{deribit_expiry_code(exp)}-{int(k)}-{opt}",
                    "ccy": ccy, "settlement": "coin",
                    "expiry": exp.isoformat(), "strike": k, "kind": opt,
                    "dte": round(dte, 3),
                    "mark_iv": round(iv_o, 3),
                    "bid": bid, "ask": ask,
                    "mid": (round((bid + ask) / 2, 4) if bid is not None and ask is not None else None),
                    "spread_pct": (0.2 if bid and ask else None),
                    "mark_price": round(max(1e-5, iv_o / 100 * math.sqrt(dte / 365) * 0.4 * spot / k), 5),
                    "last": None, "oi": round(oi, 1),
                    "volume": round(rnd.uniform() * 20, 2),
                    "volume_usd": round(rnd.uniform() * 90000, 2),
                    "underlying_price": round(fwd, 2),
                    "underlying_index": f"{ccy}-{deribit_expiry_code(exp)}",
                    "liquid": bool(bid is not None and ask is not None and oi >= MIN_OI),
                })
    out.sort(key=lambda o: (o["expiry"], o["strike"], o["kind"]))
    return out


def demo_dvol(ccy: str = "BTC", days: int = 400) -> list[dict]:
    """DVOL sintético ancorado na série REAL capturada (2024: faixa 41–85;
    set/2026: ATM ~18,7). Usa a série real de 2024 como forma e reancora o nível."""
    base = [66.81, 63.75, 65.17, 65.34, 67.64, 71.39, 71.81, 67.16, 64.17, 62.7,
            57.3, 57.42, 55.07, 56.87, 55.96, 55.16, 50.79, 48.31, 48.04, 47.96,
            47.82, 48.98, 49.59, 47.54, 45.01, 43.73, 42.43, 45.25, 45.36, 46.91,
            45.79, 44.76, 42.41, 41.52, 42.69, 42.37, 42.87, 43.82, 44.91, 47.79,
            49.91, 51.29, 54.28, 52.19, 55.13, 55.08, 55.24, 56.24, 57.24, 56.83,
            55.63, 54.01, 55.38, 52.23, 54.18, 55.17, 56.05, 58.36, 67.76, 65.54,
            65.37, 65.82, 71.72, 74.81, 73.19, 74.85, 69.68, 71.87, 75.04, 80.12,
            83.02, 78.08, 77.5, 74.58, 76.7, 75.73, 75.93, 74.08, 76.61, 77.73,
            75.26, 72.46, 73.68, 72.62, 76.25, 75.39, 75.78, 76.53, 73.11, 74.93,
            76.64, 73.81, 74.37, 72.45, 72.31, 71.96, 72.3, 77.43, 76.25, 73.69,
            72.32, 68.42, 66.94, 72.95, 71.99, 70.48, 70.88, 70.72, 69.98, 70.4,
            71.98, 72.05, 72.05, 69.37, 64.16, 62.44, 59.29, 55.12, 54.69, 56.79,
            55.33, 59.81, 61.22, 56.77, 55.88, 56.89, 58.65, 55.57, 53.76, 55.43,
            52.17, 54.27, 54.17, 54.52, 56.13, 56.23, 58.61, 57.49, 55.26, 56.67,
            57.32, 61.94, 58.59, 56.8, 53.18, 52.29, 52.21, 53.73, 53.91, 54.76,
            53.24, 53.27, 51.99, 50.68, 51.11, 52.77, 53.95, 55.62, 51.87, 50.02,
            50.34, 51.79, 50.88, 52.49, 50.56, 49.23, 48.56, 48.03, 47.9, 48.81,
            50.06, 48.6, 48.28, 47.11, 46.43, 48.23, 50.96, 48.14, 47.05, 47.94,
            45.4, 43.47, 45.44, 44.71, 42.33, 45.36, 47.53, 48.98, 48.21, 50.99,
            51.03, 50.05, 48.69, 48.54, 46.32, 49.18, 49.3, 54.52, 56.49, 58.28]
    # ÂNCORA REAL: DVOL BTC oficial em 26/set/2026 = 34,88 (série jun–set/2026:
    # mín 33,59 · máx 49,37 · média 38,97). O valor anterior aqui era 19,0 — uma
    # anotação ERRADA minha ("ATM ~18,7") que nunca foi confrontada com o índice
    # oficial. O demo estava gerando um mercado de vol 16 pontos mais barato que
    # o real, o que faria qualquer teste de VRP sobre o demo mentir.
    # ETH: não capturado -> estimativa, não medição.
    target = 34.88 if ccy == "BTC" else 41.0
    now = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(days):
        v = base[i % len(base)]
        # reancora o nível médio da série real de 2024 para o nível atual
        level = target * (v / 60.0) ** 0.65
        d = now - dt.timedelta(days=days - 1 - i)
        out.append({"ts": int(d.timestamp() * 1000), "o": round(level, 2),
                    "h": round(level * 1.03, 2), "l": round(level * 0.97, 2),
                    "c": round(level, 2)})
    return out


def _strike_ladder(spot: float, ccy: str, n: int = 13) -> list[float]:
    step = 1000.0 if ccy == "BTC" else 50.0
    center = round(spot / step) * step
    half = n // 2
    out = []
    for i in range(-half, half + 1):
        k = center + i * step
        if i in (-half, half):
            k = center + i * step * 2.5      # asas mais largas
        if k > 0:
            out.append(round(k / step) * step)
    return sorted(set(out))


class _Lcg:
    """Gerador determinístico minúsculo (sem numpy, sem random global)."""

    def __init__(self, seed: int = 42):
        self.s = (int(seed) * 1103515245 + 12345) & 0x7FFFFFFF or 1

    def _next(self) -> float:
        self.s = (self.s * 1103515245 + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF

    def uniform(self) -> float:
        return self._next()

    def normal(self) -> float:
        u1 = max(self._next(), 1e-9)
        u2 = self._next()
        return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


# ------------------------------------------------------------------ fallback
async def candles_with_fallback(ccy: str = "BTC", days: int = 400,
                                force: str | None = None,
                                deadline: float | None = None
                                ) -> tuple[list[dict], str, dict]:
    """OHLCV com cadeia de fallback (herdeiro de `fixtures_with_fallback`).

    `deribit -> coinbase -> kraken -> demo`. Devolve (velas, fonte, diagnóstico).
    Nunca levanta: o painel precisa distinguir FALHA de VAZIO, como a v2.4.1
    do FutAnalytics aprendeu do jeito difícil.

    Binance fica FORA da cadeia: geo-bloqueada no IP do Render free (EUA),
    confirmado por teste ("restricted location").
    """
    debug: dict = {"asked": [], "errors": {}}
    order = ["deribit", "coinbase", "kraken"]
    if force:
        order = [force] + [p for p in order if p != force]
    for src in order:
        debug["asked"].append(src)
        if breaker_skip(src):
            debug["errors"][src] = _skip_reason(src)
            continue
        if src == "deribit":
            coro = dr_ohlcv(ccy, "1D", days)
        elif src == "coinbase":
            coro = cb_candles(ccy, 86400, days)
        else:
            coro = kraken_ohlcv(ccy, 1440, days)
        data, err = await _guarded(coro, src, deadline)
        if err:
            debug["errors"][src] = err
            breaker_note(src, False, err)
            continue
        if data and len(data) >= 30:
            debug["used"] = src
            debug["n"] = len(data)
            breaker_note(src, True)
            return data, src, debug
        debug["errors"][src] = f"resposta curta ({len(data or [])} velas)"
        breaker_note(src, False, debug["errors"][src])
    debug["used"] = "demo"
    data = demo_candles(ccy, days)
    debug["n"] = len(data)
    debug["demo_reason"] = "todas as fontes reais falharam"
    return data, "demo", debug


async def chain_with_fallback(ccy: str = "BTC",
                              deadline: float | None = None
                              ) -> tuple[list[dict], str, dict]:
    """Superfície de opções: Deribit é a ÚNICA fonte real gratuita de IV.

    Sem Deribit não existe superfície — então aqui o fallback é honesto:
    devolve demo com aviso explícito, nunca finge que é preço de mercado.
    """
    debug: dict = {"asked": ["deribit"], "errors": {}}
    if breaker_skip("deribit"):
        debug["errors"]["deribit"] = _skip_reason("deribit")
    else:
        data, err = await _guarded(dr_option_chain(ccy), "deribit", deadline)
        if err:
            debug["errors"]["deribit"] = err
            breaker_note("deribit", False, err)
        elif data:
            debug["used"] = "deribit"
            debug["n"] = len(data)
            debug["n_liquid"] = sum(1 for o in data if o["liquid"])
            breaker_note("deribit", True)
            return data, "deribit", debug
        else:
            debug["errors"]["deribit"] = "resposta vazia"
            breaker_note("deribit", False, "resposta vazia")
    debug["used"] = "demo"
    data = demo_chain(ccy)
    debug["n"] = len(data)
    debug["n_liquid"] = sum(1 for o in data if o["liquid"])
    debug["demo_reason"] = "Deribit indisponível — IV sintética, NÃO é preço de mercado"
    return data, "demo", debug


async def dvol_with_fallback(ccy: str = "BTC", days: int = 400,
                             deadline: float | None = None
                             ) -> tuple[list[dict], str, dict]:
    debug: dict = {"asked": ["deribit"], "errors": {}}
    if breaker_skip("deribit"):
        debug["errors"]["deribit"] = _skip_reason("deribit")
    else:
        data, err = await _guarded(dr_dvol(ccy, 86400, days), "deribit", deadline)
        if err:
            debug["errors"]["deribit"] = err
            breaker_note("deribit", False, err)
        elif data:
            debug["used"] = "deribit"
            debug["n"] = len(data)
            debug["first_ts"] = data[0]["ts"]
            breaker_note("deribit", True)
            return data, "deribit", debug
        else:
            debug["errors"]["deribit"] = "série vazia"
            breaker_note("deribit", False, "série vazia")
    debug["used"] = "demo"
    data = demo_dvol(ccy, days)
    debug["n"] = len(data)
    return data, "demo", debug


# ------------------------------------------------------------------ diagnóstico
async def status(which: str = "all", ccy: str = "BTC") -> dict:
    """Testa provedores individualmente — alimenta /api/test-provider e o painel.

    Inclui o estado dos disjuntores e o orçamento de tempo, porque "por que está
    em demo?" precisa ser respondível pela tela sem ler log de servidor.

    BUG PEGO PELO BENCHMARK: este era o ÚNICO caminho que chamava provedor sem
    `_guarded` — sem timeout individual, sem deadline e sem disjuntor. E é justo
    o endpoint que o DEPLOY.md manda abrir PRIMEIRO depois do deploy. Num
    black-hole ele penduraria 4×20s = 80s, ou seja: a ferramenta de diagnóstico
    era a mais lenta do app e a que menos protegia o free tier. Agora cada
    sondagem respeita o mesmo orçamento e NÃO abre disjuntor — diagnóstico não
    deve ter efeito colateral sobre o caminho de produção.
    """
    deadline = time.monotonic() + DEADLINE_S
    out: dict = {"_budget": {"timeout_por_chamada_s": TIMEOUT,
                             "deadline_global_s": DEADLINE_S,
                             "disjuntor_abre_apos": BREAKER_FAILS,
                             "disjuntor_cooldown_s": BREAKER_COOLDOWN},
                 "_breakers": breaker_report()}

    async def probe(key: str, coro, note: str | None = None) -> None:
        t0 = time.monotonic()
        res, err = await _guarded(coro, key, deadline)
        if err:
            out[key] = {"ok": False, "error": err,
                        "ms": round((time.monotonic() - t0) * 1000)}
            if note:
                out[key]["note"] = note
            return
        info = {"ok": True, "ms": round((time.monotonic() - t0) * 1000)}
        if isinstance(res, list) and res:
            info["n"] = len(res)
            if isinstance(res[0], dict) and "c" in res[0]:
                info["last_close"] = res[-1].get("c")
        if note:
            info["note"] = note
        out[key] = info

    if which in ("all", "deribit"):
        async def _t():
            async with httpx.AsyncClient() as client:
                return await dr_rpc(client, "public/get_time", {})
        await probe("deribit_time", _t())

    if which in ("all", "chain"):
        t0 = time.monotonic()
        res, err = await _guarded(dr_option_chain(ccy), "chain", deadline)
        if err:
            out["deribit_chain"] = {"ok": False, "error": err,
                                    "ms": round((time.monotonic() - t0) * 1000)}
        else:
            ivs = [o["mark_iv"] for o in res if o.get("mark_iv") and o.get("liquid")]
            out["deribit_chain"] = {
                "ok": True, "n": len(res),
                "n_liquid": sum(1 for o in res if o.get("liquid")),
                "iv_min": round(min(ivs), 2) if ivs else None,
                "iv_max": round(max(ivs), 2) if ivs else None,
                "expiries": len({o["expiry"] for o in res}),
                "ms": round((time.monotonic() - t0) * 1000),
            }

    if which in ("all", "coinbase"):
        await probe("coinbase", cb_candles(ccy, 86400, 60))
    if which in ("all", "kraken"):
        await probe("kraken", kraken_ohlcv(ccy, 1440, 60))
    if which in ("all", "binance"):
        await probe("binance", bn_klines(ccy, "1d", 60),
                    note="esperado falhar em IP de região restrita (Render = EUA)")

    out["_breakers"] = breaker_report()
    out["_elapsed_s"] = round(time.monotonic() - (deadline - DEADLINE_S), 2)
    return out


PROVIDER_LABELS = {
    "deribit": "Deribit (superfície de IV + OHLCV)",
    "coinbase": "Coinbase Exchange (OHLCV, sem bloqueio geo)",
    "kraken": "Kraken (OHLCV, fallback 2)",
    "binance": "Binance (opcional — geo-bloqueada em IP dos EUA)",
    "demo": "Demonstração (sintético ancorado em dados reais)",
}
