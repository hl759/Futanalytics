"""FutAnalytics: plataforma de análise diária de futebol."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, provider
from .model import (
    MARKET_LABELS, TeamSample, analyze_match, kelly_stake, pick_best_market,
)

app = FastAPI(title="FutAnalytics")
db.init()

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
    min_prob: float = 55.0            # prob mínima (%) para pernas de múltipla


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
    min_prob: float | None = None


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
    team = fx[side]
    if fx["provider"] == "fd":
        return await provider.fd_team_recent(s.fd_token, team["id"], team["name"])
    if fx["provider"] == "af":
        return await provider.af_team_recent(s.af_key, team["id"], team["name"])
    return provider.demo_team_recent(day, team)


async def _analyze_fixture(s: Settings, fx: dict, day: str, with_odds: bool):
    try:
        hg, ag = await asyncio.gather(
            _team_games(s, fx, "home", day),
            _team_games(s, fx, "away", day),
        )
    except provider.ProviderError as e:
        return {**fx, "error": str(e)}

    analysis = analyze_match(
        TeamSample(fx["home"]["name"], [tuple(g) for g in hg]),
        TeamSample(fx["away"]["name"], [tuple(g) for g in ag]),
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

    best = pick_best_market(analysis, odds)
    stake = None
    if best:
        stake = kelly_stake(
            best["prob"], best["odd"], s.bankroll,
            s.kelly_fraction, s.stake_cap_pct / 100,
        )

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

    async def run(fx):
        async with sem:
            return await _analyze_fixture(s, fx, day, with_odds)

    results = await asyncio.gather(*[run(fx) for fx in fixtures])
    analyzed = [r for r in results if "analysis" in r]
    errors = [r for r in results if "error" in r]

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
    if best_single is None and ranked:
        best_single = ranked[0]

    # múltipla: 2 a 4 pernas com prob >= min_prob, jogos distintos, maior score
    legs = []
    for r in ranked:
        b = r["best"]
        if b["prob"] * 100 < s.min_prob:
            continue
        if b["ev"] is not None and b["ev"] < 0.0:
            continue  # perna com EV negativo destrói a múltipla no acumulado
        legs.append(r)
        if len(legs) == 4:
            break
    multiple = None
    if len(legs) >= 2:
        comb_odd = 1.0
        comb_prob = 1.0
        for r in legs:
            comb_odd *= r["best"]["odd"]
            comb_prob *= r["best"]["prob"]
        mstake = kelly_stake(comb_prob, comb_odd, s.bankroll, s.kelly_fraction * 0.6,
                             s.stake_cap_pct / 100 * 0.5)
        multiple = {
            "legs": [
                {
                    "fixture_id": r["id"],
                    "match": f'{r["home"]["name"]} x {r["away"]["name"]}',
                    "league": r["league"],
                    "kickoff_utc": r["kickoff_utc"],
                    "market": r["best"]["market"],
                    "label": r["best"]["label"],
                    "prob": r["best"]["prob"],
                    "odd": r["best"]["odd"],
                }
                for r in legs
            ],
            "combined_odd": round(comb_odd, 2),
            "combined_prob": round(comb_prob, 4),
            "ev": round(comb_prob * comb_odd - 1, 4),
            "stake": mstake,
        }

    return {
        "day": day,
        "provider": s.provider,
        "fixtures": analyzed,
        "errors": errors,
        "best_single": best_single["id"] if best_single else None,
        "multiple": multiple,
        "bankroll": s.bankroll,
    }


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
    # atualizar banca
    s = load_settings()
    delta = profit - (old_profit if row["status"] != "open" else 0)
    data = s.model_dump()
    data["bankroll"] = round(data["bankroll"] + delta, 2)
    db.set_setting("settings", json.dumps(data))
    return {"ok": True, "profit": profit, "bankroll": data["bankroll"]}


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
    staked = sum(r["stake"] for r in settled)
    profit = sum(r["profit"] for r in settled)
    wins = sum(1 for r in settled if r["status"] == "won")
    stats = {
        "total": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "settled": len(settled),
        "wins": wins,
        "hit_rate": round(100 * wins / len(settled), 1) if settled else None,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": round(100 * profit / staked, 2) if staked else None,
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
