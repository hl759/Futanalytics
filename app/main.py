"""FutAnalytics v3 — API.

O que mudou em relação à v2 (e por quê):

* **A escolha deixou de ser "o mais provável" e passou a ser "o que tem
  preço"**. O backtest em ~4.500 jogos de 5 ligas mostrou que a dinâmica
  "linha mais segura" devolve ROI de **−11,3%** (IC 95%: −19,1% a −3,9%), com
  60,4% de acerto: acertar muito em odd curta não paga a margem. A v3 ordena
  candidatos por **crescimento esperado de capital** (Kelly log-otimal), exige
  **odd mínima** e **vantagem sobre o mercado devigado**, e diz "não" na
  maioria dos dias.
* **Toda ficha mostra o preço-alvo** (``required_odd``) e a odd de equilíbrio
  (``breakeven_odd``): o usuário sabe exatamente por quanto vale a pena
  entrar, e por quanto não vale.
* **Dossiê do time** em cada card: descanso/congestionamento, rendimento por
  mando, execução vs. xG, volatilidade, força do calendário, bandeiras de
  risco e nota de confiança com motivos — a "análise criteriosa" em números
  auditáveis, não em adjetivos.
* **Odds do usuário**: sem odds não existe valor. O app aceita o preço da sua
  casa por mercado (``POST /api/odds/{fixture}``), guarda histórico dos preços
  e mostra **movimento da linha** (o mercado está indo para o seu lado?).
* **Múltipla com matemática**: escada de 1 a 5 pernas com probabilidade
  conjunta, correlação medida no backtest, margem efetiva e exposição diária
  limitada.
* **Segurança**: com ``APP_TOKEN`` definido, todas as rotas de leitura/escrita
  de dados exigem o token — sem ele, qualquer pessoa com a URL apaga seus
  bilhetes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sqlite3
import statistics
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import calibration, db, dossier, joint, parlay, provider, understat
from . import market as mk
from .model import (
    MARKET_LABELS,
    POLICY,
    TeamSample,
    analyze_match,
    blend_markets,
    league_priors,
    league_xg_priors,
    market_candidates,
    watchlist,
)

app = FastAPI(title="FutAnalytics", version="3.0.0")
db.init()

VERSION = "3.0.0"
STATIC = Path(__file__).resolve().parent.parent / "static"
ROOT = STATIC.parent


# ------------------------------------------------------------------ segurança


def _auth_token() -> str:
    return os.environ.get("APP_TOKEN", "").strip()


@app.middleware("http")
async def require_token(request: Request, call_next):
    """Token opcional (``APP_TOKEN``).

    Sem a variável, o app funciona aberto (uso local). Com ela definida,
    qualquer rota /api/* exige ``X-App-Token`` ou ``?token=``. É a diferença
    entre um app seu e um app público onde qualquer um apaga seus bilhetes.
    """
    token = _auth_token()
    path = request.url.path
    if token and path.startswith("/api/"):
        supplied = request.headers.get("X-App-Token") or request.query_params.get("token")
        if supplied != token:
            return JSONResponse({"detail": "token de acesso inválido ou ausente"},
                                status_code=401)
    return await call_next(request)


# ------------------------------------------------------------------ settings


class Settings(BaseModel):
    provider: str = "demo"            # demo | fd | af
    fd_token: str = ""
    af_key: str = ""
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25
    stake_cap_pct: float = 3.0
    daily_cap_pct: float = 6.0        # exposição máxima do dia (% da banca)
    min_ev: float = 3.0               # EV mínimo (%) para recomendar
    min_edge: float = 2.0             # vantagem mínima sobre o mercado (p.p.)

    # Nota sobre `model_weight`: quanto mais perto do mercado, mais "correta" a
    # probabilidade final e MENOS valor detectado (misturar dilui a divergência
    # na mesma proporção). Com 0.25 o modelo só enxergaria erro de preço de 6
    # pontos; a v3 usa 0.5 por padrão e deixa explícito que valor exige
    # discordar do mercado.
    model_weight: float = 0.5         # peso do modelo na mistura (ver nota)
    xg_weight: float = 0.65
    use_calibration: bool = True
    kelly_uncertainty: bool = True
    shadow_mode: bool = True          # registrar toda recomendação (auditoria)
    devig_method: str = "power"       # proportional | power | shin
    policy_mode: str = "value"        # value (v3) | prob (v2, legado)
    include_1x2: bool = False         # incluir resultado no cardápio de valor
    auto_bet_tracking: bool = True    # registra preço de abertura ao analisar


def load_settings() -> Settings:
    raw = db.get_setting("settings")
    s = Settings(**json.loads(raw)) if raw else Settings()
    if not s.fd_token and os.environ.get("FD_TOKEN"):
        s.fd_token = os.environ["FD_TOKEN"]
        if raw is None:
            s.provider = "fd"
    if not s.af_key and os.environ.get("AF_KEY"):
        s.af_key = os.environ["AF_KEY"]
    return s


class SettingsIn(BaseModel):
    provider: str | None = None
    fd_token: str | None = None
    af_key: str | None = None
    bankroll: float | None = None
    kelly_fraction: float | None = None
    stake_cap_pct: float | None = None
    daily_cap_pct: float | None = None
    min_ev: float | None = None
    min_edge: float | None = None
    model_weight: float | None = None
    xg_weight: float | None = None
    use_calibration: bool | None = None
    kelly_uncertainty: bool | None = None
    shadow_mode: bool | None = None
    devig_method: str | None = None
    policy_mode: str | None = None
    include_1x2: bool | None = None
    auto_bet_tracking: bool | None = None


@app.get("/api/version")
def version():
    return {"version": VERSION, "policy": POLICY, "auth": bool(_auth_token())}


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION, "time": dt.datetime.now().isoformat(timespec="seconds")}


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    d = s.model_dump()
    d["fd_token"] = ("*" * 6 + s.fd_token[-4:]) if s.fd_token else ""
    d["af_key"] = ("*" * 6 + s.af_key[-4:]) if s.af_key else ""
    d["has_fd"] = bool(s.fd_token)
    d["has_af"] = bool(s.af_key)
    d["policy"] = POLICY
    return d


_LIMITES = {
    # campo: (mínimo, máximo, mensagem)
    "bankroll": (1.0, 10_000_000.0, "banca deve ficar entre 1 e 10.000.000"),
    "kelly_fraction": (0.01, 1.0, "fração de Kelly deve ficar entre 0,01 e 1"),
    "stake_cap_pct": (0.1, 10.0, "teto por aposta deve ficar entre 0,1% e 10%"),
    "daily_cap_pct": (0.5, 50.0, "teto diário deve ficar entre 0,5% e 50%"),
    "min_ev": (0.0, 50.0, "EV mínimo deve ficar entre 0% e 50%"),
    "min_edge": (0.0, 50.0, "vantagem mínima deve ficar entre 0 e 50 pontos"),
    "model_weight": (0.0, 1.0, "peso do modelo deve ficar entre 0 e 1"),
    "xg_weight": (0.0, 1.0, "peso do xG deve ficar entre 0 e 1"),
}
_PROVIDERS = ("fd", "af", "demo")
_POLICY_MODES = ("value", "prob")


def _validate_settings(body: SettingsIn) -> None:
    """Recusa configurações que quebrariam a gestão de risco.

    Um app de apostas que aceita ``model_weight = 7`` ou ``bankroll = -100``
    silenciosamente está a um clique de produzir números sem sentido.
    """
    dados = body.model_dump(exclude_none=True)
    for campo, (lo, hi, msg) in _LIMITES.items():
        if campo in dados and not (lo <= float(dados[campo]) <= hi):
            raise HTTPException(400, msg)
    if "provider" in dados and dados["provider"] not in _PROVIDERS:
        raise HTTPException(400, f"provedor inválido (use {'/'.join(_PROVIDERS)})")
    if "devig_method" in dados and dados["devig_method"] not in mk.DEVIG_METHODS:
        raise HTTPException(400, f"método de devig inválido (use {'/'.join(mk.DEVIG_METHODS)})")
    if "policy_mode" in dados and dados["policy_mode"] not in _POLICY_MODES:
        raise HTTPException(400, f"modo de política inválido (use {'/'.join(_POLICY_MODES)})")


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    _validate_settings(body)
    s = load_settings()
    data = s.model_dump()
    for k, v in body.model_dump(exclude_none=True).items():
        if k in ("fd_token", "af_key") and v.startswith("*"):
            continue
        data[k] = v
    db.set_setting("settings", json.dumps(data))
    return {"ok": True}


# ------------------------------------------------------------------ modelo


CALIB_CACHE: dict = {"mtime": None, "data": None}


def _calibrators(mode: str) -> dict:
    """Curvas do modo pedido; recarrega se o arquivo mudou (a v2 nunca recarregava)."""
    try:
        mtime = calibration.CALIB_PATH.stat().st_mtime
    except OSError:
        return {}
    if CALIB_CACHE["mtime"] != mtime:
        CALIB_CACHE["data"] = calibration.load_all()
        CALIB_CACHE["mtime"] = mtime
    return (CALIB_CACHE["data"] or {}).get(mode, {})


@app.get("/api/model-info")
def model_info():
    cals = calibration.load_all()
    return {
        "version": VERSION,
        "calibration_loaded": bool(cals),
        "calibration_modes": {m: sorted(v.keys()) for m, v in cals.items()},
        "xg_leagues": sorted(understat.UNDERSTAT_LEAGUES.keys()),
        "policy": POLICY,
        "devig_methods": list(mk.DEVIG_METHODS),
        "parlay_rho": {
            "same_league_same_family": parlay.RHO_SAME_LEAGUE_SAME_FAMILY,
            "same_league": parlay.RHO_SAME_LEAGUE,
            "measured_note": "rho de Over 2.5 na mesma rodada medido no backtest",
        },
    }


# ------------------------------------------------------------------ odds


def _odds_store_key(fixture_id: str) -> str:
    return f"odds:user:{fixture_id}"


class OddsIn(BaseModel):
    odds: dict[str, float]
    source: str = "manual"


@app.post("/api/odds/{fixture_id}")
def save_odds(fixture_id: str, body: OddsIn):
    """Odds do usuário (a odd da sua casa) para um jogo.

    Guarda também um **histórico de preços**: o app mostra o movimento da
    linha desde a primeira leitura — quando o mercado anda na direção da sua
    entrada, isso é confirmação; quando anda contra, é aviso.
    """
    clean = {}
    for k, v in (body.odds or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 1.01 and k in MARKET_LABELS:
            clean[k] = round(fv, 3)
    if not clean:
        raise HTTPException(400, "nenhuma odd válida enviada")
    db.cache_set(_odds_store_key(fixture_id), {"odds": clean, "source": body.source,
                                               "at": dt.datetime.now().isoformat(timespec="seconds")},
                 7 * 86400)
    db.odds_snapshot(fixture_id, clean)
    return {"ok": True, "saved": clean,
            "movement": db.odds_movement(fixture_id)}


@app.get("/api/odds/{fixture_id}")
def get_odds(fixture_id: str):
    stored = db.cache_get(_odds_store_key(fixture_id)) or {}
    return {"odds": stored.get("odds", {}), "movement": db.odds_movement(fixture_id)}


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
    if s.xg_weight > 0:
        try:
            us = await understat.team_history(fx["league"], team["name"])
            if us and len(us) >= 6:
                return us
        except Exception:
            pass
    if fx["provider"] == "fd":
        return await provider.fd_team_recent(s.fd_token, team["id"], team["name"])
    if fx["provider"] == "af":
        return await provider.af_team_recent(s.af_key, team["id"], team["name"])
    return provider.demo_team_recent(day, team)


async def _team_games_for(s: Settings, fx: dict, day: str):
    try:
        hg, ag = await asyncio.gather(_team_games(s, fx, "home", day),
                                      _team_games(s, fx, "away", day))
        return hg, ag, None
    except provider.ProviderError as e:
        return None, None, str(e)


async def _analyze_fixture(s: Settings, fx: dict, hg, ag, home_avg, away_avg,
                           xg_home_avg, xg_away_avg, with_odds: bool):
    ts_home = TeamSample(fx["home"]["name"], [tuple(g) for g in hg])
    ts_away = TeamSample(fx["away"]["name"], [tuple(g) for g in ag])
    lam_override = None
    ratings = None
    league_matches = None
    if s.xg_weight > 0 and fx["league"] in understat.UNDERSTAT_LEAGUES:
        try:
            league_matches = await understat.league_matches(fx["league"])
            if league_matches and len(league_matches) >= joint.MIN_MATCHES:
                key = f"joint:{fx['league']}:{date.today()}"
                model = db.cache_get(key)
                if model is None:
                    model = joint.fit_league(league_matches, date.today())
                    if model is not None:
                        db.cache_set(key, model, 12 * 3600)
                if model is not None:
                    titles = sorted({m["home"] for m in league_matches}
                                    | {m["away"] for m in league_matches})
                    us_h = understat.match_understat_team(fx["home"]["name"], titles)
                    us_a = understat.match_understat_team(fx["away"]["name"], titles)
                    if us_h and us_a:
                        lam_override = joint.predict_lambdas(model, us_h, us_a)
                        ratings = model
        except Exception:
            pass

    analysis = analyze_match(ts_home, ts_away, home_avg=home_avg, away_avg=away_avg,
                             xg_home_avg=xg_home_avg, xg_away_avg=xg_away_avg,
                             xg_weight=s.xg_weight, lam_override=lam_override)

    # contexto da liga + H2H (usa o cache do Understat; zero requisição extra)
    league_env = dossier.league_environment(league_matches) if league_matches else {}
    if league_matches:
        try:
            ctx = await understat.league_context(fx["league"], fx["home"]["name"],
                                                 fx["away"]["name"])
            if ctx:
                analysis["h2h"] = ctx.get("h2h", [])
        except Exception:
            pass

    # dossiê criterioso do confronto
    analysis["dossier"] = dossier.build(
        ts_home, ts_away, lam_home=analysis["lambda_home"], lam_away=analysis["lambda_away"],
        ratings=ratings, league=league_env)
    analysis["n_eff"] = (analysis["sample_home"] + analysis["sample_away"]) / 2

    odds = {}
    if with_odds:
        if fx["provider"] == "af":
            try:
                odds = await provider.af_odds(s.af_key, int(fx["id"].split("-")[1])) or {}
            except provider.ProviderError:
                odds = {}
        elif fx["provider"] == "demo":
            odds = provider.demo_odds(fx["id"], analysis["fair_odds"])
    stored = db.cache_get(_odds_store_key(fx["id"])) or {}
    if stored.get("odds"):
        odds = {**odds, **stored["odds"]}   # odd do usuário manda (é a que ele consegue)

    if s.use_calibration:
        mode = "joint" if lam_override is not None else ("xg" if analysis.get("xg_used") else "goals")
        calibrated = calibration.apply_calibration(analysis["markets"], _calibrators(mode))
        if calibrated != analysis["markets"]:
            analysis["model_markets"] = analysis["markets"]
            analysis["calibrated"] = True
            analysis["markets"] = calibrated
            analysis["fair_odds"] = {
                k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in calibrated.items()}

    if odds:
        blended = blend_markets(analysis["markets"], odds, s.model_weight,
                                method=s.devig_method)
        if "model_markets" not in analysis:
            analysis["model_markets"] = analysis["markets"]
        analysis["markets"] = blended
        analysis["fair_odds"] = {
            k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in blended.items()}

    odds_movement = db.odds_movement(fx["id"])
    value = None
    near = []
    if odds:
        kw = {"mode": "singles", "n_eff": analysis["n_eff"], "devig_method": s.devig_method,
                  "kelly_mult": s.kelly_fraction, "cap": s.stake_cap_pct / 100.0,
                  "uncertainty": s.kelly_uncertainty}
        value = _apply_thresholds(
            _first_eligible(analysis, odds, kw), s)
        near = watchlist(analysis, odds, limit=3, **kw)
        if s.include_1x2:
            for card in market_candidates(analysis, odds, mode="resultado",
                                          markets=("home", "draw", "away"),
                                          n_eff=analysis["n_eff"],
                                          devig_method=s.devig_method, kelly_mult=s.kelly_fraction,
                                          cap=s.stake_cap_pct / 100.0,
                                          uncertainty=s.kelly_uncertainty):
                if card["take"] and (value is None or card["growth"] > value["growth"]):
                    value = _apply_thresholds(card, s)
                    break

    stake = None
    if value:
        stake = mk.stake_fraction(value["prob"], value["odd"], kelly_mult=s.kelly_fraction,
                                  cap=s.stake_cap_pct / 100.0, n_eff=analysis["n_eff"],
                                  uncertainty=s.kelly_uncertainty)
        stake = {"stake": round(s.bankroll * stake["kelly_frac"], 2),
                 "pct": stake["pct"], "kelly_full": stake["kelly_full"],
                 "p_used": stake["p_used"]}

    return {**fx, "analysis": analysis, "odds": odds or None, "value": value, "stake": stake,
            "watchlist": near, "odds_movement": odds_movement,
            "has_odds": bool(odds)}


def _first_eligible(analysis: dict, odds: dict, kw: dict):
    for card in market_candidates(analysis, odds, **kw):
        if card["take"]:
            return card
    return None


def _apply_thresholds(card: dict | None, s: Settings):
    """Aplica os limites do usuário (EV e vantagem) sobre a ficha do candidato.

    Um candidato só passa se (a) a própria ficha o considera elegível
    (``take``: faixa de probabilidade/odd, EV, vantagem, Kelly) e (b) os
    limites do usuário são respeitados. Sem o teste de ``take``, mercados
    fora da faixa (ex.: odd 2,92) vazavam para dentro da múltipla.
    """
    if not card or not card.get("take"):
        return None
    if card["ev"] * 100 < s.min_ev:
        return None
    if card["edge"] is not None and card["edge"] * 100 < s.min_edge:
        return None
    return card


@app.get("/api/day")
async def day_analysis(day: str | None = None):
    s = load_settings()
    day = day or str(date.today())
    try:
        fixtures = await _load_fixtures(s, day)
    except provider.ProviderError as e:
        raise HTTPException(502, str(e)) from e

    with_odds = s.provider in ("af", "demo")
    sem = asyncio.Semaphore(2 if s.provider == "fd" else 5)

    async def fetch(fx):
        async with sem:
            return fx, await _team_games_for(s, fx, day)

    fetched = await asyncio.gather(*[fetch(fx) for fx in fixtures])

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

    async def finish(fx, hg, ag):
        async with sem:
            home_avg, away_avg = priors[fx["league"]]
            xh, xa_ = xg_priors[fx["league"]]
            return await _analyze_fixture(s, fx, hg, ag, home_avg, away_avg, xh, xa_, with_odds)

    analyzed = await asyncio.gather(*[finish(fx, hg, ag) for fx, hg, ag in ready])

    eligible = [r for r in analyzed if r.get("value")]
    eligible.sort(key=lambda r: -(r["value"]["growth"] * (1 + r["value"]["ev"])))
    best_single = eligible[0]["id"] if eligible else None

    # múltipla (pernas elegíveis por valor, escada 1..4, correlação e teto diário)
    leg_cands = []
    for r in analyzed:
        if not r.get("odds"):
            continue
        n_eff = r["analysis"]["n_eff"]
        for card in market_candidates(r["analysis"], r["odds"], mode="legs", n_eff=n_eff,
                                      devig_method=s.devig_method,
                                      kelly_mult=s.kelly_fraction,
                                      cap=s.stake_cap_pct / 100.0,
                                      uncertainty=s.kelly_uncertainty):
            card = _apply_thresholds(card, s)
            if not card:
                continue
            leg_cands.append({
                "fixture_id": r["id"], "match": f'{r["home"]["name"]} x {r["away"]["name"]}',
                "league": r["league"], "kickoff_utc": r["kickoff_utc"],
                "market": card["market"], "label": card["label"], "prob": card["prob"],
                "odd": card["odd"], "market_prob": card["market_prob"], "edge": card["edge"],
                "ev": card["ev"], "growth": card["growth"], "family": card["family"],
                "n_eff": n_eff,
            })
    best_per_fixture: dict = {}
    for c in leg_cands:
        cur = best_per_fixture.get(c["fixture_id"])
        if cur is None or c["growth"] > cur["growth"]:
            best_per_fixture[c["fixture_id"]] = c
    multiple = parlay.build(list(best_per_fixture.values()), kelly_mult=s.kelly_fraction,
                            cap=s.stake_cap_pct / 100.0, daily_cap_pct=s.daily_cap_pct,
                            uncertainty=s.kelly_uncertainty)
    if multiple.get("slip"):
        multiple["slip"]["stake_value"] = round(
            s.bankroll * multiple["slip"]["stake_pct"] / 100.0, 2)
    for leg in multiple.get("legs", []):
        leg["anchor"] = leg["odd"] < 1.45

    exposure = parlay.daily_exposure(
        [multiple["slip"]] if multiple.get("available") else [],
        singles_pct=sum(r["stake"]["pct"] for r in eligible[:3] if r.get("stake")),
        daily_cap_pct=s.daily_cap_pct)

    recusados = [r for r in analyzed if not r.get("value")]
    summary = {
        "fixtures": len(analyzed),
        "with_odds": sum(1 for r in analyzed if r.get("has_odds")),
        "entradas": len(eligible),
        "recusados": len(recusados),
        "exposure": exposure,
        "worst_gap": _top_rejection_reasons(recusados),
        "note": ("Dia sem entrada é resultado normal e esperado: o filtro de "
                 "valor recusa a maior parte dos jogos." if not eligible else
                 "Entradas ranqueadas por crescimento esperado de capital."),
    }

    if s.shadow_mode:
        for r in eligible[:3]:
            _log_shadow(day, r)
        for leg in multiple.get("legs", []):
            fx = next((r for r in analyzed if r["id"] == leg["fixture_id"]), None)
            if fx:
                _log_shadow_leg(day, leg)

    return {
        "day": day, "provider": s.provider, "fixtures": analyzed, "errors": errors,
        "best_single": best_single, "multiple": multiple,
        "bankroll": s.bankroll, "shadow_mode": s.shadow_mode, "summary": summary,
        "policy": POLICY, "devig_method": s.devig_method,
    }


def _top_rejection_reasons(recusados: list) -> list:
    counter: dict[str, int] = {}
    for r in recusados:
        for c in (r.get("watchlist") or []):
            for reason in c.get("reasons", []):
                key = reason.split("(")[0].split(":")[0].strip()
                counter[key] = counter.get(key, 0) + 1
    return [{"reason": k, "n": v} for k, v in sorted(counter.items(), key=lambda kv: -kv[1])[:5]]


def _log_shadow(day: str, r: dict):
    v = r.get("value")
    if not v:
        return
    _insert_shadow_bet(day, f'{r["home"]["name"]} x {r["away"]["name"]}', v["market"],
                       v["label"], v["odd"], r["stake"]["stake"] if r.get("stake") else 0,
                       v["prob"])


def _log_shadow_leg(day: str, leg: dict):
    _insert_shadow_bet(day, leg["match"], leg["market"], leg["label"], leg["odd"], 0,
                       leg["prob"])


def _insert_shadow_bet(day, label, market_key, selection, odd, stake, prob):
    try:
        with db.conn() as c:
            exists = c.execute(
                "SELECT 1 FROM bets WHERE shadow=1 AND match_date=? AND label=? AND selection=?",
                (day, label, selection)).fetchone()
            if exists:
                return
            c.execute(
                "INSERT INTO bets(created_at, match_date, label, market, selection, odd, stake, prob, ev, shadow) "
                "VALUES(?,?,?,?,?,?,?,?,?,1)",
                (dt.datetime.now().isoformat(timespec="seconds"), day, label, market_key,
                 selection, odd, stake, prob, round((prob or 0) * odd - 1, 4)))
    except Exception:
        pass


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
    edge: float | None = None


@app.post("/api/bets")
def add_bet(b: BetIn):
    ev = round((b.prob or 0) * b.odd - 1, 4) if b.prob else None
    with db.conn() as c:
        cur = c.execute(
            "INSERT INTO bets(created_at, match_date, label, market, selection, odd, stake, prob, ev, is_multiple, legs) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (dt.datetime.now().isoformat(timespec="seconds"), b.match_date, b.label, b.market,
             b.selection, b.odd, b.stake, b.prob, ev, 1 if b.is_multiple else 0,
             json.dumps(b.legs) if b.legs else None))
        bet_id = cur.lastrowid
    return {"ok": True, "id": bet_id}


class SettleIn(BaseModel):
    status: str


@app.post("/api/bets/{bet_id}/settle")
def settle_bet(bet_id: int, body: SettleIn):
    if body.status not in ("won", "lost", "void", "open"):
        raise HTTPException(400, "status inválido")
    with db.conn() as c:
        row = c.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not row:
            raise HTTPException(404, "aposta não encontrada")
        old_profit = row["profit"] or 0
        old_status = row["status"]
        if body.status == "won":
            profit = round(row["stake"] * (row["odd"] - 1), 2)
        elif body.status == "lost":
            profit = -row["stake"]
        else:
            profit = 0.0
        c.execute("UPDATE bets SET status=?, profit=? WHERE id=?", (body.status, profit, bet_id))
    if not row["shadow"]:
        s = load_settings()
        delta = profit - (old_profit if old_status != "open" else 0)
        data = s.model_dump()
        data["bankroll"] = round(data["bankroll"] + delta, 2)
        db.set_setting("settings", json.dumps(data))
        return {"ok": True, "profit": profit, "bankroll": data["bankroll"]}
    return {"ok": True, "profit": profit}


class ClosingIn(BaseModel):
    closing_odd: float


@app.post("/api/bets/{bet_id}/closing")
def set_closing(bet_id: int, body: ClosingIn):
    if body.closing_odd <= 1.0:
        raise HTTPException(400, "odd de fechamento deve ser > 1.0")
    with db.conn() as c:
        row = c.execute("SELECT odd FROM bets WHERE id=?", (bet_id,)).fetchone()
        if not row:
            raise HTTPException(404, "aposta não encontrada")
        clv = round(100 * (row["odd"] / body.closing_odd - 1), 2)
        c.execute("UPDATE bets SET closing_odd=?, clv=? WHERE id=?", (body.closing_odd, clv, bet_id))
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
            try:
                r["legs"] = json.loads(r["legs"])
            except ValueError:
                r["legs"] = None
    shadow, real = [r for r in rows if r["shadow"]], [r for r in rows if not r["shadow"]]
    stats = _bet_stats(real, rows)
    # auditoria do modelo: acerto prometido vs realizado no modo sombra
    settled_shadow = [r for r in shadow if r["status"] in ("won", "lost")]
    if settled_shadow:
        stats["shadow_hit"] = round(100 * sum(1 for r in settled_shadow if r["status"] == "won")
                                    / len(settled_shadow), 1)
        probs = [r["prob"] for r in settled_shadow if r["prob"]]
        stats["shadow_promised"] = round(100 * statistics.mean(probs), 1) if probs else None
        stats["shadow_n"] = len(settled_shadow)
        clvs = [r["clv"] for r in shadow if r["clv"] is not None]
        stats["shadow_clv"] = round(statistics.mean(clvs), 2) if clvs else None
    return {"bets": rows, "stats": stats}


def _bet_stats(real: list[dict], rows: list[dict]) -> dict:
    settled = [r for r in real if r["status"] in ("won", "lost")]
    staked = sum(r["stake"] for r in settled)
    profit = sum(r["profit"] for r in settled)
    wins = sum(1 for r in settled if r["status"] == "won")
    clvs = [r["clv"] for r in real if r["clv"] is not None]
    sharpes = [r["profit"] / r["stake"] for r in settled if r["stake"]]
    mean_r = statistics.mean(sharpes) if sharpes else None
    sd_r = statistics.pstdev(sharpes) if len(sharpes) > 1 else None
    return {
        "total": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "settled": len(settled),
        "wins": wins,
        "hit_rate": round(100 * wins / len(settled), 1) if settled else None,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": round(100 * profit / staked, 2) if staked else None,
        # significância honesta do ROI (erro-padrão do retorno médio)
        "roi_sigma": round(100 * sd_r / math_sqrt(len(sharpes)), 2)
        if sharpes and sd_r else None,
        "t_stat": round(mean_r / (sd_r / math_sqrt(len(sharpes))), 2)
        if sharpes and sd_r and len(sharpes) > 1 else None,
        "shadow_count": sum(1 for r in rows if r["shadow"]),
        "clv_avg": round(statistics.mean(clvs), 2) if clvs else None,
        "clv_beat_pct": round(100 * sum(1 for c in clvs if c > 0) / len(clvs), 1) if clvs else None,
        "clv_n": len(clvs),
    }


def math_sqrt(x: float) -> float:
    import math
    return math.sqrt(x) if x > 0 else 0.0


@app.get("/api/labels")
def labels():
    return {"labels": MARKET_LABELS, "policy": POLICY}


@app.get("/api/backtest-summary")
def backtest_summary():
    """Resumo do último backtest (gerado por ``app.backtest --json``).

    Serve para o painel mostrar, com honestidade, o que o modelo demonstrou —
    e o que não demonstrou.
    """
    f = ROOT / "backtest_summary.json"
    if not f.exists():
        return {"available": False,
                "hint": "Rode: python -m app.backtest --fit-calibration --json backtest_summary.json"}
    try:
        return {"available": True, **json.loads(f.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"available": False}


# ------------------------------------------------------------------ backup


_BET_COLS = ["id", "created_at", "match_date", "label", "market", "selection", "odd",
             "stake", "prob", "ev", "is_multiple", "legs", "status", "profit",
             "shadow", "closing_odd", "clv"]


@app.get("/api/backup")
def export_backup():
    with db.conn() as c:
        bets = [dict(r) for r in c.execute("SELECT * FROM bets ORDER BY id").fetchall()]
        raw = c.execute("SELECT value FROM settings WHERE key='settings'").fetchone()
    payload = {
        "app": "futanalytics", "schema": 3,
        "exported_at": dt.datetime.now().isoformat(timespec="seconds"),
        "settings": json.loads(raw["value"]) if raw else {},
        "bets": [{k: b.get(k) for k in _BET_COLS} for b in bets],
    }
    fname = f'futanalytics-backup-{date.today().isoformat()}.json'
    return JSONResponse(content=payload,
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.post("/api/backup")
def import_backup(payload: dict):
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
                c.execute(f"INSERT INTO bets({', '.join(_BET_COLS)}) "
                          f"VALUES({', '.join('?' * len(_BET_COLS))})",
                          tuple(row[k] for k in _BET_COLS))
                n += 1
            except (ValueError, TypeError, sqlite3.Error):
                continue
    if isinstance(settings, dict) and settings:
        db.set_setting("settings", json.dumps(settings))
    return {"ok": True, "bets": n}


@app.get("/api/test-provider")
async def test_provider(which: str):
    s = load_settings()
    try:
        if which == "af":
            if not s.af_key:
                return {"ok": False, "msg": "Nenhuma chave da API-Football salva."}
            info = await provider.af_status(s.af_key)
            return {"ok": True, "msg": f"Chave válida · plano {info['plan']} · "
                                       f"uso {info['requests_today']}/{info['requests_limit']}."}
        if which == "fd":
            if not s.fd_token:
                return {"ok": False, "msg": "Nenhum token da football-data.org salvo."}
            info = await provider.fd_status(s.fd_token)
            return {"ok": True, "msg": "Token válido. Competições: "
                                       + ", ".join(info["competitions"][:6]) + "."}
        if which == "us":
            games = await understat.team_history("Premier League", "Manchester City")
            n = len(games or [])
            if n:
                return {"ok": True, "msg": f"Understat OK: {n} jogos com xG."}
            return {"ok": False, "msg": "Understat indisponível agora (motor segue sem xG)."}
        return {"ok": False, "msg": "Provedor desconhecido."}
    except provider.ProviderError as e:
        return {"ok": False, "msg": str(e)}
    except Exception as e:
        return {"ok": False, "msg": f"Falha de conexão: {e}"}


# ------------------------------------------------------------------ frontend

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))
