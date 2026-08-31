"""
Camada de dados: busca jogos do dia e histórico recente dos times.

Provedores suportados:
- football-data.org (v4): tier gratuito cobre Brasileirão Série A, Premier League,
  La Liga, Serie A, Bundesliga, Ligue 1, Champions League e mais. Limite 10 req/min.
- API-Football (api-sports.io v3): tier gratuito 100 req/dia; cobre odds reais.
- demo: dados simulados realistas para testar a plataforma sem chave.

Tudo é cacheado em SQLite para economizar requisições.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import random
from datetime import date, timedelta

import httpx

from . import db

FD_BASE = "https://api.football-data.org/v4"
AF_BASE = "https://v3.football.football-api-sports.io"
AF_BASE_ALT = "https://v3.football.api-sports.io"

# football-data.org: competições do tier gratuito que interessam
FD_COMPETITIONS = {
    "BSA": "Brasileirão Série A",
    "CL": "Champions League",
    "PL": "Premier League",
    "PD": "La Liga",
    "SA": "Serie A (Itália)",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "PPL": "Primeira Liga (Portugal)",
    "DED": "Eredivisie",
    "ELC": "Championship (Inglaterra)",
}

# API-Football: ligas prioritárias (id, nome)
AF_LEAGUES = {
    71: "Brasileirão Série A",
    72: "Brasileirão Série B",
    73: "Copa do Brasil",
    13: "Libertadores",
    2: "Champions League",
    39: "Premier League",
    140: "La Liga",
    135: "Serie A (Itália)",
    78: "Bundesliga",
    61: "Ligue 1",
}


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------- football-data
async def _fd_get(client: httpx.AsyncClient, path: str, token: str, params=None):
    for attempt in range(4):
        r = await client.get(
            f"{FD_BASE}{path}",
            headers={"X-Auth-Token": token},
            params=params or {},
            timeout=25,
        )
        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "15")) + 1
            await asyncio.sleep(min(wait, 65))
            continue
        if r.status_code in (400, 403):
            raise ProviderError(f"football-data.org recusou ({r.status_code}): {r.text[:200]}")
        r.raise_for_status()
        return r.json()
    raise ProviderError("Limite de requisições da football-data.org excedido; tente em 1 minuto.")


BR_TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Sao_Paulo


def _local_day(utc_iso: str) -> str:
    """Converte kickoff UTC para a data local do Brasil (UTC-3)."""
    d = dt.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    return str(d.astimezone(BR_TZ).date())


async def fd_fixtures(token: str, day: str):
    key = f"fd:fixtures:v2:{day}"
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    # Janela de 3 dias: o dateTo da API se comporta como limite aberto em
    # consultas de dia único, e jogos noturnos no Brasil caem no dia seguinte
    # em UTC. Buscamos a janela e filtramos pela data local (UTC-3).
    d0 = dt.date.fromisoformat(day)
    async with httpx.AsyncClient() as client:
        data = await _fd_get(
            client, "/matches", token,
            {"dateFrom": str(d0 - timedelta(days=1)), "dateTo": str(d0 + timedelta(days=1))},
        )
    out = []
    for m in data.get("matches", []):
        code = m.get("competition", {}).get("code")
        if code not in FD_COMPETITIONS:
            continue
        utc = m.get("utcDate")
        if not utc or _local_day(utc) != day:
            continue
        out.append({
            "id": f"fd-{m['id']}",
            "provider": "fd",
            "league": FD_COMPETITIONS[code],
            "kickoff_utc": utc,
            "status": m.get("status"),
            "home": {"id": m["homeTeam"]["id"], "name": m["homeTeam"]["name"]},
            "away": {"id": m["awayTeam"]["id"], "name": m["awayTeam"]["name"]},
        })
    ttl = 3600 if day >= str(date.today()) else 7 * 86400
    db.cache_set(key, out, ttl)
    return out


async def fd_team_recent(token: str, team_id: int, team_name: str):
    key = f"fd:team:{team_id}"
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    today = date.today()
    async with httpx.AsyncClient() as client:
        data = await _fd_get(
            client, f"/teams/{team_id}/matches", token,
            {
                "status": "FINISHED",
                "dateFrom": str(today - timedelta(days=240)),
                "dateTo": str(today),
                "limit": 30,
            },
        )
    games = []
    for m in sorted(data.get("matches", []), key=lambda x: x["utcDate"], reverse=True)[:18]:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        played = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).date()
        days_ago = (today - played).days
        is_home = m["homeTeam"]["id"] == team_id
        gf, ga = (hg, ag) if is_home else (ag, hg)
        games.append([days_ago, is_home, gf, ga])
    db.cache_set(key, games, 12 * 3600)
    return games


# ---------------------------------------------------------------- API-Football
def _af_translate_error(errors: dict) -> str:
    """Converte erros da API-Football em mensagem acionável em português."""
    joined = " ".join(str(v) for v in errors.values())
    low = joined.lower()
    if "not have access to this season" in low or "try from 2021" in low:
        return (
            "O plano GRATUITO da API-Football não dá mais acesso à temporada atual "
            "(só libera dados de 2021 a 2023). Para jogos reais de hoje sem pagar, "
            "troque o provedor para football-data.org em Configurações "
            "(token grátis em football-data.org/client/register). "
            "A API-Football atual só serve com plano pago."
        )
    if "invalid api key" in low or "token" in errors:
        return (
            "Chave da API-Football inválida. Confira se copiou a chave do painel "
            "dashboard.api-football.com (aba Account > API Key), sem espaços. "
            "Atenção: a chave do RapidAPI é diferente da chave direta da api-sports.io; "
            "esta plataforma usa a chave direta."
        )
    if "request limit" in low or "rate limit" in low or "too many" in low:
        return "Limite de requisições da API-Football atingido. Aguarde e tente novamente."
    return f"API-Football retornou erro: {joined}"


async def _af_get(client: httpx.AsyncClient, path: str, key: str, params=None):
    today = str(date.today())
    if db.api_usage_today(today) >= 95:
        raise ProviderError("Orçamento diário da API-Football (100 req) quase esgotado; usando apenas cache.")
    last_err = None
    for base in (AF_BASE_ALT,):
        try:
            r = await client.get(
                f"{base}{path}",
                headers={"x-apisports-key": key},
                params=params or {},
                timeout=25,
            )
            db.api_usage_inc(today)
            data = r.json()
            errs = data.get("errors")
            if errs and isinstance(errs, dict):
                raise ProviderError(_af_translate_error(errs))
            return data
        except (httpx.HTTPError, ValueError) as e:
            last_err = e
    raise ProviderError(f"Falha de conexão com a API-Football: {last_err}")


async def af_status(key: str) -> dict:
    """Valida a chave e retorna plano/uso; também testa acesso à temporada atual."""
    async with httpx.AsyncClient() as client:
        data = await _af_get(client, "/status", key)
        resp = data.get("response") or {}
        sub = resp.get("subscription", {}) or {}
        req = resp.get("requests", {}) or {}
        info = {
            "ok": True,
            "plan": sub.get("plan", "?"),
            "requests_today": req.get("current", 0),
            "requests_limit": req.get("limit_day", 0),
        }
        # teste real: o plano consegue ver jogos de hoje?
        try:
            fx = await _af_get(client, "/fixtures", key, {"date": str(date.today())})
            info["current_season_ok"] = bool(fx.get("response")) or not fx.get("errors")
        except ProviderError as e:
            info["current_season_ok"] = False
            info["season_error"] = str(e)
        return info


async def fd_status(token: str) -> dict:
    """Valida o token da football-data.org listando as competições acessíveis."""
    async with httpx.AsyncClient() as client:
        data = await _fd_get(client, "/competitions", token)
    comps = [c.get("code") for c in data.get("competitions", [])]
    covered = [FD_COMPETITIONS[c] for c in comps if c in FD_COMPETITIONS]
    return {"ok": True, "competitions": covered}


async def af_fixtures(key: str, day: str):
    ck = f"af:fixtures:{day}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    async with httpx.AsyncClient() as client:
        data = await _af_get(client, "/fixtures", key, {"date": day, "timezone": "America/Sao_Paulo"})
    out = []
    for f in data.get("response", []):
        lg = f.get("league", {})
        if lg.get("id") not in AF_LEAGUES:
            continue
        fx = f.get("fixture", {})
        teams = f.get("teams", {})
        out.append({
            "id": f"af-{fx['id']}",
            "provider": "af",
            "league": AF_LEAGUES[lg["id"]],
            "kickoff_utc": fx.get("date"),
            "status": fx.get("status", {}).get("short"),
            "home": {"id": teams["home"]["id"], "name": teams["home"]["name"]},
            "away": {"id": teams["away"]["id"], "name": teams["away"]["name"]},
        })
    db.cache_set(ck, out, 3600)
    return out


async def af_team_recent(key: str, team_id: int, team_name: str):
    ck = f"af:team:{team_id}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    async with httpx.AsyncClient() as client:
        data = await _af_get(client, "/fixtures", key, {"team": team_id, "last": 15})
    today = date.today()
    games = []
    for f in data.get("response", []):
        goals = f.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None or ag is None:
            continue
        played = dt.datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
        days_ago = (today - played).days
        is_home = f["teams"]["home"]["id"] == team_id
        gf, ga = (hg, ag) if is_home else (ag, hg)
        games.append([days_ago, is_home, gf, ga])
    db.cache_set(ck, games, 12 * 3600)
    return games


async def af_odds(key: str, fixture_id: int):
    ck = f"af:odds:{fixture_id}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    async with httpx.AsyncClient() as client:
        data = await _af_get(client, "/odds", key, {"fixture": fixture_id})
    odds = {}
    try:
        bms = data["response"][0]["bookmakers"]
    except (IndexError, KeyError):
        bms = []
    for bm in bms[:3]:
        for bet in bm.get("bets", []):
            name = bet.get("name", "")
            for v in bet.get("values", []):
                val, odd = str(v.get("value")), float(v.get("odd", 0) or 0)
                mk = None
                if name == "Match Winner":
                    mk = {"Home": "home", "Draw": "draw", "Away": "away"}.get(val)
                elif name == "Goals Over/Under":
                    if val == "Over 1.5": mk = "over_1.5"
                    elif val == "Over 2.5": mk = "over_2.5"
                    elif val == "Over 3.5": mk = "over_3.5"
                    elif val == "Under 2.5": mk = "under_2.5"
                    elif val == "Under 3.5": mk = "under_3.5"
                elif name == "Both Teams Score":
                    mk = {"Yes": "btts_yes", "No": "btts_no"}.get(val)
                elif name == "Double Chance":
                    mk = {"Home/Draw": "dc_1x", "Draw/Away": "dc_x2", "Home/Away": "dc_12"}.get(val)
                if mk and odd > 1.0:
                    odds[mk] = max(odds.get(mk, 0), odd)  # melhor odd entre casas
    db.cache_set(ck, odds, 2 * 3600)
    return odds


# ---------------------------------------------------------------- demo
_DEMO_TEAMS = [
    ("Flamengo", 1.9, 0.9), ("Palmeiras", 1.8, 0.8), ("Botafogo", 1.5, 1.0),
    ("São Paulo", 1.3, 1.0), ("Internacional", 1.4, 1.1), ("Cruzeiro", 1.5, 0.9),
    ("Bahia", 1.3, 1.2), ("Fortaleza", 1.1, 1.3), ("Manchester City", 2.1, 0.9),
    ("Arsenal", 1.8, 0.7), ("Liverpool", 2.0, 1.0), ("Barcelona", 2.2, 1.1),
    ("Real Madrid", 2.0, 0.9), ("Bayern", 2.3, 1.0), ("Inter de Milão", 1.9, 0.8),
    ("PSG", 2.1, 0.9),
]
_DEMO_LEAGUES = ["Brasileirão Série A", "Premier League", "La Liga", "Champions League"]


def _demo_games(rng: random.Random, atk: float, deff: float):
    games = []
    for k in range(14):
        is_home = rng.random() < 0.5
        base_for = atk * (1.15 if is_home else 0.9)
        base_ag = deff * (0.9 if is_home else 1.15)
        gf = min(rng.poissonvariate(base_for) if hasattr(rng, "poissonvariate") else _pois(rng, base_for), 6)
        ga = min(_pois(rng, base_ag), 6)
        games.append([4 + k * 6 + rng.randint(0, 3), is_home, gf, ga])
    return games


def _pois(rng: random.Random, lam: float) -> int:
    import math
    L = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def demo_fixtures(day: str):
    rng = random.Random(day)
    teams = _DEMO_TEAMS[:]
    rng.shuffle(teams)
    out = []
    n = 6
    for i in range(n):
        h, a = teams[i * 2], teams[i * 2 + 1]
        hour = rng.choice([16, 18, 19, 21])
        out.append({
            "id": f"demo-{day}-{i}",
            "provider": "demo",
            "league": rng.choice(_DEMO_LEAGUES),
            "kickoff_utc": f"{day}T{hour:02d}:00:00Z",
            "status": "SCHEDULED",
            "home": {"id": 1000 + i * 2, "name": h[0], "_atk": h[1], "_def": h[2]},
            "away": {"id": 1001 + i * 2, "name": a[0], "_atk": a[1], "_def": a[2]},
        })
    return out


def demo_team_recent(day: str, team: dict):
    rng = random.Random(f"{day}-{team['name']}")
    return _demo_games(rng, team.get("_atk", 1.4), team.get("_def", 1.1))


def demo_odds(fixture_id: str, fair: dict):
    """Odds de mercado simuladas: fair odds com margem de casa (~5%) e ruído."""
    rng = random.Random(fixture_id)
    odds = {}
    for mk, fo in fair.items():
        # margem média de 5% com dispersão entre casas: às vezes a melhor odd
        # do mercado supera a justa (é exatamente aí que existe valor real)
        noise = rng.uniform(0.94, 1.10)
        odds[mk] = round(max(fo * 0.95 * noise, 1.01), 2)
    return odds
