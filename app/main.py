"""FutAnalytics v2: plataforma de análise diária de futebol.

v2 — ganhos validados em backtest (app/backtest.py → backtest_report.md):
- histórico com xG do Understat quando a liga tem cobertura;
- dois horizontes de força (estrutural + forma recente);
- calibração isotônica das probabilidades do modelo (modos xg/goals);
- peso do modelo vs. mercado reduzido (0.25) — o mercado manda, o modelo filtra;
- Kelly com desconto de incerteza amostral;
- modo sombra (toda recomendação vira bilhete de auditoria) e CLV:
  registro da odd de fechamento para medir edge de verdade.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import calibration, db, provider, understat
from .model import (
    MARKET_LABELS, TeamSample, analyze_match, blend_markets, build_multiple,
    kelly_stake, league_priors, league_xg_priors, pick_best_market,
)

app = FastAPI(title="FutAnalytics")
db.init()

VERSION = "2.0.0"

STATIC = Path(__file__).resolve().parent.parent / "static"


# ------------------------------------------------------------------ settings
class Settings(BaseModel):
    provider: str = "demo"            # demo | fd | af
    fd_token: str = ""
    af_key: str = ""
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25
    stake_cap_pct: float = 3.0
    min_ev: float = 3.0               # EV mínimo (%) para recomendar aposta
    model_weight: float = 0.25        # peso do modelo na mistura (v2: 0.25)
    xg_weight: float = 0.65           # peso do xG sobre gols brutos (0 = desligar)
    use_calibration: bool = True      # aplicar curvas isotônicas treinadas
    kelly_uncertainty: bool = True    # Kelly desconta incerteza da amostra
    shadow_mode: bool = False         # registrar toda recomendação como bilhete-sombra


def load_settings() -> Settings:
    raw = db.get_setting("settings")
    if raw:
        s = Settings(**json.loads(raw))
    else:
        s = Settings()
    # Em hospedagem (Render etc.) o disco pode ser apagado a cada deploy;
    # variáveis de ambiente garantem que token e provedor sobrevivam.
    import os
    if not s.fd_token and os.environ.get("FD_TOKEN"):
        s.fd_token = os.environ["FD_TOKEN"]
        if raw is None:
            s.provider = "fd"
    if not s.af_key and os.environ.get("AF_KEY"):
        s.af_key = os.environ["AF_KEY"]
    return s


# ------------------------------------------------------------------ backup
# O Render free apaga o disco a cada deploy; estes endpoints permitem baixar
# TUDO que importa (configurações + bilhetes, incluindo CLV e modo sombra) em
# um JSON portátil e restaurar depois. Cache e contadores de API não vão no
# arquivo: são transitórios e inflariam o backup à toa.

_BET_COLS = ["id", "created_at", "match_date", "label", "market", "selection",
             "odd", "stake", "prob", "ev", "is_multiple", "legs", "status",
             "profit", "shadow", "closing_odd", "clv"]


@app.get("/api/backup")
def export_backup():
    with db.conn() as c:
        bets = [dict(r) for r in c.execute(
            "SELECT * FROM bets ORDER BY id").fetchall()]
        raw = c.execute("SELECT value FROM settings WHERE key='settings'").fetchone()
    payload = {
        "app": "futanalytics",
        "schema": 2,
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "settings": json.loads(raw["value"]) if raw else {},
        "bets": [{k: b.get(k) for k in _BET_COLS} for b in bets],
    }
    fname = f'futanalytics-backup-{date.today().isoformat()}.json'
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/backup")
def import_backup(payload: dict):
    """Restaura um backup: substitui bilhetes e configurações atuais.

    Preserva ids dos bilhetes. Colunas que não existiam na versão que gerou o
    arquivo entram com o padrão do schema atual (compatível com backups v1).
    """
    if payload.get("app") != "futanalytics":
        raise HTTPException(400, "Este arquivo não é um backup do FutAnalytics.")
    bets = payload.get("bets") or []
    settings = payload.get("settings")
    n = 0
    with db.conn() as c:
        c.execute("DELETE FROM bets")
        for b in bets:
            try:
                row = {k: b.get(k) for k in _BET_COLS}
                row["id"] = int(row["id"] or 0)
                row["odd"] = float(row["odd"] or 0)
                row["stake"] = float(row["stake"] or 0)
                row["shadow"] = 1 if row["shadow"] else 0
                row["is_multiple"] = 1 if row["is_multiple"] else 0
                c.execute(
                    f"INSERT INTO bets({', '.join(_BET_COLS)}) "
                    f"VALUES({', '.join('?' * len(_BET_COLS))})",
                    tuple(row[k] for k in _BET_COLS),
                )
                n += 1
            except (ValueError, TypeError, sqlite3.Error):
                continue  # bilhete corrompido no arquivo: pula, não aborta
    if isinstance(settings, dict) and settings:
        db.set_setting("settings", json.dumps(settings))
    return {"ok": True, "bets": n}


@app.get("/api/version")
def version():
    return {"version": VERSION}


@app.get("/api/model-info")
def model_info():
    """Diagnóstico do motor: quais recursos da v2 estão ativos."""
    cals = calibration.load_all()
    return {
        "version": VERSION,
        "calibration_loaded": bool(cals),
        "calibration_modes": {m: sorted(v.keys()) for m, v in cals.items()},
        "xg_leagues": sorted(understat.UNDERSTAT_LEAGUES.keys()),
        "params": {
            "model_weight_default": 0.25,
            "form_weight": 0.25,
            "decay_long_halflife_days": round(0.6931 / 0.012, 0),
            "decay_short_halflife_days": round(0.6931 / 0.06, 0),
        },
    }


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    d = s.model_dump()
    # não vazar chaves completas para o front
    d["fd_token"] = ("*" * 6 + s.fd_token[-4:]) if s.fd_token else ""
    d["af_key"] = ("*" * 6 + s.af_key[-4:]) if s.af_key else ""
    d["has_fd"] = bool(s.fd_token)
    d["has_af"] = bool(s.af_key)
    return d


class SettingsIn(BaseModel):
    provider: str | None = None
    fd_token: str | None = None
    af_key: str | None = None
    bankroll: float | None = None
    kelly_fraction: float | None = None
    stake_cap_pct: float | None = None
    min_ev: float | None = None
    model_weight: float | None = None
    xg_weight: float | None = None
    use_calibration: bool | None = None
    kelly_uncertainty: bool | None = None
    shadow_mode: bool | None = None


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    s = load_settings()
    data = s.model_dump()
    for k, v in body.model_dump(exclude_none=True).items():
        if k in ("fd_token", "af_key") and v.startswith("*"):
            continue  # máscara devolvida, não sobrescrever
        data[k] = v
    db.set_setting("settings", json.dumps(data))
    return {"ok": True}


@app.get("/api/test-provider")
async def test_provider(which: str):
    """Diagnóstico de conexão: valida a chave e explica qualquer problema."""
    s = load_settings()
    try:
        if which == "af":
            if not s.af_key:
                return {"ok": False, "msg": "Nenhuma chave da API-Football salva. Cole a chave e clique em Salvar antes de testar."}
            info = await provider.af_status(s.af_key)
            msg = f"Chave válida · plano {info['plan']} · uso hoje {info['requests_today']}/{info['requests_limit']}."
            if not info.get("current_season_ok"):
                msg += " PORÉM: " + info.get(
                    "season_error",
                    "o plano não retornou jogos da temporada atual.",
                )
                return {"ok": False, "msg": msg}
            return {"ok": True, "msg": msg + " Acesso à temporada atual confirmado."}
        if which == "fd":
            if not s.fd_token:
                return {"ok": False, "msg": "Nenhum token da football-data.org salvo. Cole o token e clique em Salvar antes de testar."}
            info = await provider.fd_status(s.fd_token)
            comps = ", ".join(info["competitions"][:6])
            return {"ok": True, "msg": f"Token válido. Competições cobertas: {comps}."}
        if which == "us":
            games = await understat.team_history("Premier League", "Manchester City")
            n = len(games or [])
            if n:
                return {"ok": True, "msg": f"Understat OK: {n} jogos com xG recuperados para Manchester City."}
            return {"ok": False, "msg": "Understat indisponível neste momento (o motor segue com gols brutos + calibração modo goals)."}
        return {"ok": False, "msg": "Provedor desconhecido."}
    except provider.ProviderError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:
        return {"ok": False, "msg": f"Falha de conexão: {e}"}


# ------------------------------------------------------------------ análise
async def _load_fixtures(s: Settings, day: str):
    if s.provider == "fd":
        if not s.fd_token:
            raise HTTPException(400, "Configure o token da football-data.org em Configurações.")
        return await provider.fd_fixtures(s.fd_token, day)
    if s.provider == "af":
        if not s.af_key:
            raise HTTPException(400, "Configure a chave da API-Football em Configurações.")
        return await provider.af_fixtures(s.af_key, day)
    return provider.demo_fixtures(day)


async def _team_games(s: Settings, fx: dict, side: str, day: str):
    """Histórico do time; prioriza Understat (gols + xG), cai para o provedor."""
    team = fx[side]
    # xG do Understat para as ligas cobertas (não consome cota das APIs pagas).
    # Com xg_weight = 0 (kill-switch nas Configurações) nem consulta.
    if s.xg_weight > 0:
        try:
            us = await understat.team_history(fx["league"], team["name"])
            if us and len(us) >= 6:
                return us
        except Exception:
            pass  # understat fora do ar: segue sem xG
    if fx["provider"] == "fd":
        return await provider.fd_team_recent(s.fd_token, team["id"], team["name"])
    if fx["provider"] == "af":
        return await provider.af_team_recent(s.af_key, team["id"], team["name"])
    return provider.demo_team_recent(day, team)


async def _team_games_for(s: Settings, fx: dict, day: str):
    """Busca o histórico dos dois times, tratando erro por jogo."""
    try:
        hg, ag = await asyncio.gather(
            _team_games(s, fx, "home", day),
            _team_games(s, fx, "away", day),
        )
        return hg, ag, None
    except provider.ProviderError as e:
        return None, None, str(e)


_CALIB_CACHE: dict = {"data": None}


def _get_calibrators(mode: str) -> dict:
    """Curvas do modo pedido; recarrega se o arquivo mudou."""
    if _CALIB_CACHE["data"] is None:
        _CALIB_CACHE["data"] = calibration.load_all()
    return (_CALIB_CACHE["data"] or {}).get(mode, {})


async def _analyze_fixture(s: Settings, fx: dict, hg, ag, home_avg, away_avg,
                           xg_home_avg, xg_away_avg, with_odds: bool):
    """Analisa um jogo com histórico já baixado e priores de liga específicos."""
    analysis = analyze_match(
        TeamSample(fx["home"]["name"], [tuple(g) for g in hg]),
        TeamSample(fx["away"]["name"], [tuple(g) for g in ag]),
        home_avg=home_avg, away_avg=away_avg,
        xg_home_avg=xg_home_avg, xg_away_avg=xg_away_avg,
        xg_weight=s.xg_weight,
    )

    odds = None
    if with_odds:
        if fx["provider"] == "af":
            try:
                odds = await provider.af_odds(s.af_key, int(fx["id"].split("-")[1]))
                odds = odds or None
            except provider.ProviderError:
                odds = None
        elif fx["provider"] == "demo":
            odds = provider.demo_odds(fx["id"], analysis["fair_odds"])

    # v2: calibra as probabilidades do modelo ANTES de misturar com o mercado.
    # As curvas são por modo (xg/goals) porque a distribuição muda com o insumo.
    if s.use_calibration:
        mode = "xg" if analysis.get("xg_used") else "goals"
        calibrated = calibration.apply_calibration(analysis["markets"], _get_calibrators(mode))
        if calibrated != analysis["markets"]:
            analysis["model_markets"] = analysis["markets"]
            analysis["calibrated"] = True
            analysis["markets"] = calibrated
            analysis["fair_odds"] = {
                k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in calibrated.items()
            }

    # Mistura com o consenso do mercado (margem removida), peso configurável.
    if odds:
        blended = blend_markets(analysis["markets"], odds, s.model_weight)
        if "model_markets" not in analysis:
            analysis["model_markets"] = analysis["markets"]
        analysis["markets"] = blended
        analysis["fair_odds"] = {
            k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in blended.items()
        }

    best = pick_best_market(analysis, odds)
    n_eff = (analysis["sample_home"] + analysis["sample_away"]) / 2
    stake = None
    if best:
        stake = kelly_stake(
            best["prob"], best["odd"], s.bankroll,
            s.kelly_fraction, s.stake_cap_pct / 100,
            n_eff=n_eff, uncertainty=s.kelly_uncertainty,
        )

    analysis["n_eff"] = round(n_eff, 1)
    return {**fx, "analysis": analysis, "odds": odds, "best": best, "stake": stake}


@app.get("/api/day")
async def day_analysis(day: str | None = None):
    s = load_settings()
    day = day or str(date.today())
    try:
        fixtures = await _load_fixtures(s, day)
    except provider.ProviderError as e:
        raise HTTPException(502, str(e))

    with_odds = s.provider in ("af", "demo")
    # limitar concorrência para respeitar rate limits
    sem = asyncio.Semaphore(2 if s.provider == "fd" else 5)

    # Fase 1: baixar o histórico dos times de todos os jogos do dia.
    async def fetch(fx):
        async with sem:
            return fx, await _team_games_for(s, fx, day)

    fetched = await asyncio.gather(*[fetch(fx) for fx in fixtures])

    # Médias de gols e de xG por liga (mando/visitante) a partir do histórico
    # do dia, com shrinkage para as médias globais quando a amostra é pequena.
    pool: dict[str, list] = {}
    errors: list = []
    ready: list = []
    for fx, (hg, ag, err) in fetched:
        if err:
            errors.append({**fx, "error": err})
            continue
        pool.setdefault(fx["league"], [])
        pool[fx["league"]].extend(hg)
        pool[fx["league"]].extend(ag)
        ready.append((fx, hg, ag))
    priors = {lg: league_priors(games) for lg, games in pool.items()}
    xg_priors = {lg: league_xg_priors(games) for lg, games in pool.items()}

    # Fase 2: analisar cada jogo (odds, calibração, mistura, melhor mercado).
    async def finish(fx, hg, ag):
        async with sem:
            home_avg, away_avg = priors[fx["league"]]
            xh, xa_ = xg_priors[fx["league"]]
            return await _analyze_fixture(s, fx, hg, ag, home_avg, away_avg, xh, xa_, with_odds)

    analyzed = await asyncio.gather(*[finish(fx, hg, ag) for fx, hg, ag in ready])

    # ranking do dia: score = prob*confiança (+EV quando há odds)
    ranked = sorted(
        (r for r in analyzed if r.get("best")),
        key=lambda r: -(r["best"]["score"] * r["analysis"]["confidence"]),
    )

    best_single = None
    for r in ranked:
        b = r["best"]
        if b["ev"] is not None and b["ev"] * 100 < s.min_ev:
            continue
        best_single = r
        break

    # múltipla: montada como um apostador montaria: melhor ângulo por jogo,
    # mercados diversificados, até 4 pernas, no máximo 2 do mesmo tipo e 1 âncora
    multiple = build_multiple(analyzed, s)

    # modo sombra: registrar recomendações como bilhetes de auditoria (dedupe)
    if s.shadow_mode:
        if best_single and best_single.get("best"):
            _log_shadow(day, best_single)
        if multiple:
            for leg in multiple["legs"]:
                fx = next((r for r in analyzed if r["id"] == leg["fixture_id"]), None)
                if fx:
                    _log_shadow_leg(day, fx, leg)

    return {
        "day": day,
        "provider": s.provider,
        "fixtures": analyzed,
        "errors": errors,
        "best_single": best_single["id"] if best_single else None,
        "multiple": multiple,
        "bankroll": s.bankroll,
        "shadow_mode": s.shadow_mode,
    }


def _log_shadow(day: str, r: dict):
    _insert_shadow_bet(
        day, f'{r["home"]["name"]} x {r["away"]["name"]}',
        r["best"]["market"], r["best"]["label"], r["best"]["odd"],
        r["stake"]["stake"] if r.get("stake") else 0, r["best"]["prob"],
    )


def _log_shadow_leg(day: str, fx: dict, leg: dict):
    _insert_shadow_bet(day, leg["match"], leg["market"], leg["label"],
                       leg["odd"], 0, leg["prob"])


def _insert_shadow_bet(day, label, market, selection, odd, stake, prob):
    try:
        with db.conn() as c:
            exists = c.execute(
                "SELECT 1 FROM bets WHERE shadow=1 AND match_date=? AND selection=? AND status='open'",
                (day, selection),
            ).fetchone()
            if exists:
                return
            c.execute(
                "INSERT INTO bets(created_at, match_date, label, market, selection, odd, stake, prob, ev, shadow) "
                "VALUES(?,?,?,?,?,?,?,?,?,1)",
                (dt.datetime.now().isoformat(timespec="seconds"), day, label, market,
                 selection, odd, stake, prob,
                 round((prob or 0) * odd - 1, 4) if prob else None),
            )
    except Exception:
        pass  # sombra nunca quebra o painel


# ------------------------------------------------------------------ bilhetes
class BetIn(BaseModel):
    match_date: str
    label: str
    market: str
    selection: str
    odd: float
    stake: float
    prob: float | None = None
    is_multiple: bool = False
    legs: list | None = None


@app.post("/api/bets")
def add_bet(b: BetIn):
    ev = round((b.prob or 0) * b.odd - 1, 4) if b.prob else None
    with db.conn() as c:
        c.execute(
            "INSERT INTO bets(created_at, match_date, label, market, selection, odd, stake, prob, ev, is_multiple, legs) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                dt.datetime.now().isoformat(timespec="seconds"),
                b.match_date, b.label, b.market, b.selection,
                b.odd, b.stake, b.prob, ev,
                1 if b.is_multiple else 0,
                json.dumps(b.legs) if b.legs else None,
            ),
        )
    return {"ok": True}


class SettleIn(BaseModel):
    status: str  # won | lost | void


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: int, body: SettleIn):
    if body.status not in ("won", "lost", "void", "open"):
        raise HTTPException(400, "status inválido")
    with db.conn() as c:
        row = c.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not row:
            raise HTTPException(404, "aposta não encontrada")
        old_profit = row["profit"] or 0
        if body.status == "won":
            profit = round(row["stake"] * (row["odd"] - 1), 2)
        elif body.status == "lost":
            profit = -row["stake"]
        else:
            profit = 0.0
        c.execute("UPDATE bets SET status=?, profit=? WHERE id=?", (body.status, profit, bet_id))
    # atualizar banca (bilhetes-sombra não mexem na banca: são auditoria)
    if not row["shadow"]:
        s = load_settings()
        delta = profit - (old_profit if row["status"] != "open" else 0)
        data = s.model_dump()
        data["bankroll"] = round(data["bankroll"] + delta, 2)
        db.set_setting("settings", json.dumps(data))
        return {"ok": True, "profit": profit, "bankroll": data["bankroll"]}
    return {"ok": True, "profit": profit}


class ClosingIn(BaseModel):
    closing_odd: float


@app.post("/api/bets/{bet_id}/closing")
def set_closing(bet_id: int, body: ClosingIn):
    """Registra a odd de FECHAMENTO e calcula o CLV.

    CLV = (odd_pegada / odd_fechamento - 1). Consistentemente positivo em
    300+ apostas é o melhor indício de edge real antes do lucro aparecer.
    """
    if body.closing_odd <= 1.0:
        raise HTTPException(400, "odd de fechamento deve ser > 1.0")
    with db.conn() as c:
        row = c.execute("SELECT odd FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not row:
            raise HTTPException(404, "aposta não encontrada")
        clv = round(100 * (row["odd"] / body.closing_odd - 1), 2)
        c.execute("UPDATE bets SET closing_odd=?, clv=? WHERE id=?",
                  (body.closing_odd, clv, bet_id))
    return {"ok": True, "clv": clv}


@app.delete("/api/bets/{bet_id}")
def delete_bet(bet_id: int):
    with db.conn() as c:
        c.execute("DELETE FROM bets WHERE id=?", (bet_id,))
    return {"ok": True}


@app.get("/api/bets")
def list_bets():
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM bets ORDER BY id DESC").fetchall()]
    for r in rows:
        if r.get("legs"):
            r["legs"] = json.loads(r["legs"])
    settled = [r for r in rows if r["status"] in ("won", "lost")]
    staked = sum(r["stake"] for r in settled if not r["shadow"])
    profit = sum(r["profit"] for r in settled if not r["shadow"])
    wins = sum(1 for r in settled if r["status"] == "won")
    shadow = [r for r in rows if r["shadow"]]
    clvs = [r["clv"] for r in rows if r["clv"] is not None]
    stats = {
        "total": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "settled": len(settled),
        "wins": wins,
        "hit_rate": round(100 * wins / len(settled), 1) if settled else None,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": round(100 * profit / staked, 2) if staked else None,
        "shadow_count": len(shadow),
        "shadow_settled": sum(1 for r in shadow if r["status"] in ("won", "lost")),
        "clv_avg": round(sum(clvs) / len(clvs), 2) if clvs else None,
        "clv_beat_pct": round(100 * sum(1 for c in clvs if c > 0) / len(clvs), 1) if clvs else None,
        "clv_n": len(clvs),
    }
    return {"bets": rows, "stats": stats}


@app.get("/api/labels")
def labels():
    return MARKET_LABELS


# ------------------------------------------------------------------ frontend
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))
