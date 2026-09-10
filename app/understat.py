"""Provedor de xG: Understat (grátis, sem chave).

O Understat expõe o JSON da liga em GET /getLeagueData/{liga}/{temporada}
(temporada = ano de início, ex.: 2025 = 2025/26). De lá extraímos, por time,
o histórico recente com gols E gols esperados por jogo — o insumo que o
backtest mostrou faltar no motor v1.

Cobertura: Premier League, La Liga, Bundesliga, Serie A, Ligue 1 e RFPL.
Não há Brasileirão no Understat; nessas ligas o motor segue com gols brutos
(mas com os outros ganhos da v2: dois horizontes + calibração modo "goals").

Sem dependências novas: usa httpx (já do projeto) e cache SQLite existente.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import difflib
import unicodedata

import httpx

from . import db

US_TIMEOUT = 10  # s: se o Understat estiver lento, o painel segue sem xG

# dedup de buscas concorrentes: na 1ª carga do dia várias análises podem pedir
# a mesma liga ao mesmo tempo; só a primeira dispara HTTP, as outras esperam.
_pending: dict[str, asyncio.Future] = {}

US_BASE = "https://understat.com"
US_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

# liga do app (código fd ou id af já resolvido para nome interno) -> slug understat
UNDERSTAT_LEAGUES = {
    "Premier League": "EPL",
    "La Liga": "La_Liga",
    "Bundesliga": "Bundesliga",
    "Serie A (Itália)": "Serie_A",
    "Ligue 1": "Ligue_1",
}

# apelidos comuns: nome do provedor -> título no Understat
_TEAM_ALIASES = {
    "internazionale": "Inter",
    "inter": "Internazionale",
    "inter de milao": "Internazionale",
    "psg": "Paris Saint Germain",
    "paris saint-germain": "Paris Saint Germain",
    "paris sg": "Paris Saint Germain",
    "paris saint germain": "Paris Saint Germain",
    "bayern de munique": "Bayern Munich",
    "bayern munchen": "Bayern Munich",
    "bayern": "Bayern Munich",
    "sporting cp": "Sporting",
    "manchester utd": "Manchester United",
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "man city": "Manchester City",
    "manchester city": "Manchester City",
    "newcastle utd": "Newcastle United",
    "wolves": "Wolverhampton Wanderers",
    "ath bilbao": "Athletic Club",
    "athletic bilbao": "Athletic Club",
    "ath madrid": "Atletico Madrid",
    "atletico de madrid": "Atletico Madrid",
    "tottenham hotspur": "Tottenham",
    "west ham united": "West Ham",
    "leeds united": "Leeds",
    "leicester city": "Leicester",
    "real betis balompie": "Betis",
    "club atletico de madrid": "Atletico Madrid",
    "atletico de madrid": "Atletico Madrid",
    "atletico madrid": "Atletico Madrid",
    "real sociedad": "Real Sociedad",
    " athletic club": "Athletic Club",
    "athletic club bilbao": "Athletic Club",
    "celta de vigo": "Celta Vigo",
    "deportivo alaves": "Alaves",
    "rayo vallecano": "Rayo Vallecano",
    "vfb stuttgart": "Stuttgart",
    "bayer 04 leverkusen": "Leverkusen",
    "bayer leverkusen": "Leverkusen",
    "borussia dortmund": "Dortmund",
    "borussia monchengladbach": "Gladbach",
    "bor. monchengladbach": "Gladbach",
    "borussia m'gladbach": "Gladbach",
    "1. fsv mainz 05": "Mainz 05",
    "mainz 05": "Mainz 05",
    "eintracht frankfurt": "Eintracht Frankfurt",
    "fc koln": "FC Cologne",
    "1. fc union berlin": "Union Berlin",
    "union berlin": "Union Berlin",
    "rb leipzig": "RB Leipzig",
    "sc freiburg": "Freiburg",
    "sv werder bremen": "Werder Bremen",
    "werder bremen": "Werder Bremen",
    "fc augsburg": "Augsburg",
    "vfl wolfsburg": "Wolfsburg",
    "vfl bochum": "Bochum",
    "tsg hoffenheim": "Hoffenheim",
    "1899 hoffenheim": "Hoffenheim",
    "fc heidenheim": "Heidenheim",
    "ac milan": "Milan",
    "internazionale milano": "Internazionale",
    "fc internazionale milano": "Internazionale",
    "as roma": "Roma",
    "ss lazio": "Lazio",
    "ssc napoli": "Napoli",
    "atalanta bc": "Atalanta",
    "fiorentina": "Fiorentina",
    "bologna fc": "Bologna",
    "torino fc": "Torino",
    "udinese calcio": "Udinese",
    "us sassuolo calcio": "Sassuolo",
    "genoa cfc": "Genoa",
    "empoli fc": "Empoli",
    "cagliari calcio": "Cagliari",
    "hellas verona fc": "Verona",
    "parma calcio": "Parma",
    "como 1907": "Como",
    "venezia fc": "Venezia",
    "monza": "Monza",
    "lecce": "Lecce",
    "olympique lyon": "Lyon",
    "olympique lyonnais": "Lyon",
    "paris saint germain fc": "Paris Saint Germain",
    "as monaco": "Monaco",
    "losc lille": "Lille",
    "ogc nice": "Nice",
    "rc lens": "Lens",
    "stade rennais fc": "Rennes",
    "fc nantes": "Nantes",
    "toulouse fc": "Toulouse",
    "stade brestois 29": "Brest",
    "stade de reims": "Reims",
    "montpellier herault sc": "Montpellier",
    "rc striasbourg": "Strasbourg",
    "rc strasbourg alsace": "Strasbourg",
    "fc lorient": "Lorient",
    "le havre ac": "Le Havre",
    "aj auxerre": "Auxerre",
    "angers sco": "Angers",
    "as saint-etienne": "Saint-Etienne",
    "dynamo kyiv": "Dynamo Kyiv",
}


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for ch in ".,'’-":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def match_understat_team(name: str, us_titles: list[str]) -> str | None:
    """Resolve o nome do provedor para o título do Understat."""
    norm = normalize_name(name)
    if norm in _TEAM_ALIASES:
        return _TEAM_ALIASES[norm]
    by_norm = {normalize_name(t): t for t in us_titles}
    if norm in by_norm:
        return by_norm[norm]
    # contém (ex.: "Manchester United U21" não existe aqui, mas garante)
    hits = [t for nt, t in by_norm.items() if norm in nt or nt in norm]
    if len(hits) == 1:
        return hits[0]
    close = difflib.get_close_matches(norm, list(by_norm), n=1, cutoff=0.8)
    if close:
        return by_norm[close[0]]
    return None


def current_season(today: dt.date | None = None) -> int:
    d = today or dt.date.today()
    return d.year if d.month >= 7 else d.year - 1


async def _fetch_league_data(slug: str, season: int) -> dict | None:
    key = f"us:league:{slug}:{season}"
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    # buscas concorrentes da mesma liga compartilham um único resultado
    existing = _pending.get(key)
    if existing is not None:
        return await asyncio.shield(existing)
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _pending[key] = fut
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                f"{US_BASE}/getLeagueData/{slug}/{season}",
                headers=US_HEADERS, timeout=US_TIMEOUT,
            )
        data = None
        if r.status_code == 200:
            try:
                raw = r.json()
                dates = raw.get("dates") or []
                if isinstance(dates, str):
                    dates = []
                data = []
                for m in dates:
                    try:
                        data.append({
                            "isResult": bool(m.get("isResult")),
                            "h": (m.get("h") or {}).get("title"),
                            "a": (m.get("a") or {}).get("title"),
                            "hg": int((m.get("goals") or {}).get("h", -1)),
                            "ag": int((m.get("goals") or {}).get("a", -1)),
                            "xgh": float((m.get("xG") or {}).get("h", -1) or -1),
                            "xga": float((m.get("xG") or {}).get("a", -1) or -1),
                            "datetime": m.get("datetime"),
                        })
                    except (ValueError, TypeError, AttributeError):
                        continue
            except ValueError:
                data = None
        if data is not None:
            ttl = 12 * 3600 if season >= current_season() else 7 * 86400
            db.cache_set(key, data, ttl)
        if not fut.done():
            fut.set_result(data)
        return data
    except Exception:
        # falha de rede/parse: todos os que esperavam seguem sem xG (fallback)
        if not fut.done():
            fut.set_result(None)
        return None
    finally:
        _pending.pop(key, None)


def _days_ago(dt_str: str, today: dt.date) -> int | None:
    try:
        d = dt.datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return (today - d).days


async def team_history(league_name: str, team_name: str,
                       lookback_days: int = 240, max_games: int = 40) -> list | None:
    """Histórico [(dias_atras, foi_mandante, gp, gc, xgp, xgc)] do time, ou None.

    None significa "sem dado de xG para este time/liga" — o chamador cai no
    histórico de gols do provedor principal.
    """
    slug = UNDERSTAT_LEAGUES.get(league_name)
    if not slug:
        return None
    today = dt.date.today()
    seasons = [current_season(today)]
    # início de temporada: completa com a anterior
    if today.month <= 9:
        seasons.append(seasons[0] - 1)

    all_matches = []
    titles: list[str] = []
    for s in seasons:
        data = await _fetch_league_data(slug, s)
        if not data:
            continue
        all_matches.extend(data)
        titles = sorted({m["h"] for m in data if m["h"]} | {m["a"] for m in data if m["a"]}) or titles
    if not all_matches:
        return None

    us_team = match_understat_team(team_name, titles)
    if not us_team:
        return None

    games = []
    for m in all_matches:
        if not m["isResult"] or m["hg"] < 0:
            continue
        if m["h"] != us_team and m["a"] != us_team:
            continue
        da = _days_ago(m["datetime"], today)
        if da is None or da < 0 or da > lookback_days:
            continue
        is_home = m["h"] == us_team
        gf, ga = (m["hg"], m["ag"]) if is_home else (m["ag"], m["hg"])
        xgf, xga = (m["xgh"], m["xga"]) if is_home else (m["xga"], m["xgh"])
        games.append([da, is_home, gf, ga,
                      xgf if xgf >= 0 else None, xga if xga >= 0 else None])
    games.sort(key=lambda g: g[0])
    return games[:max_games]
