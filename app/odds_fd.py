"""Odds REAIS grátis e sem chave: football-data.co.uk (arquivo fixtures.csv).

Contexto (2026): não existe mais API de odds de futebol gratuita de verdade —
The Odds API tirou soccer do plano free, API-Football free só tem temporadas
antigas. O que continua grátis, sem chave e sem cota de uso é o arquivo público
do football-data.co.uk com as odds das PRÓXIMAS rodadas das ~22 ligas
europeias que ele cobre: casa, empate, fora e Over/Under 2.5 de
**Bet365 e Pinnacle** (a Pinnacle é a casa mais "sharp" do mundo — a odd que
os traders usam como referência de probabilidade real).

O que este módulo faz:
1. Baixa o fixtures.csv UMA vez e cacheia por 12h (≈2 downloads/dia, ~150 KB).
2. Faz o casamento jogo do app (football-data.org / API-Football) ↔ linha do
   CSV por data + nomes normalizados/apelidos/fuzzy.
3. Devolve as odds REAIS por mercado (melhor preço entre B365 e Pinnacle).
4. DERIVA as demais linhas (Over 1.5/3.5, Unders, BTTS, totais por time) a
   partir do consenso do mercado por INVERSÃO POISSON: encontra os λ implícitos
   no 1X2 + Over 2.5 do mercado e projeta as outras linhas com a mesma matriz
   Dixon-Coles do motor — técnica padrão de precificação das próprias casas.
   Essas odds vêm marcadas como "derivada" para você nunca confundir com real.

Ligas cobertas (futebol europeu). Brasileirão e Champions não estão no
arquivo: nesses jogos o app segue mostrando a ODD JUSTA do modelo, como antes
— explícito na tela, sem fingir preço de mercado.

Zero dependências novas (httpx já existe). Se o download falhar (site fora),
o painel segue com odds justas — nunca quebra.
"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import io
import math
import unicodedata

import httpx

from . import db
from .model import de_vig_markets, poisson_pmf, score_matrix

FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
TIMEOUT = 18
CACHE_KEY = "fduk:fixtures:v3"
STALE_KEY = "fduk:fixtures:stale:v3"
CACHE_TTL = 12 * 3600          # ~2 downloads por dia, nada além disso
STALE_TTL = 7 * 86400          # último arquivo bom, se o site cair

# liga interna do app -> código "Div" do football-data.co.uk
LEAGUE_TO_DIV = {
    "Premier League": "E0",
    "Championship (Inglaterra)": "E1",
    "La Liga": "SP1",
    "Serie A (Itália)": "I1",
    "Bundesliga": "D1",
    "Ligue 1": "F1",
    "Eredivisie": "N1",
    "Primeira Liga (Portugal)": "P1",
}

# apelidos de times (nomes normalizados de provedores/CSV) -> nome no CSV
# cobre os casos em que o fuzzy difflib não chega (siglas, abreviações locais)
_ALIASES = {
    # Premier League
    "manchester city": "Man City", "man city": "Man City",
    "manchester united": "Man United", "man utd": "Man United", "man united": "Man United",
    "tottenham": "Tottenham", "tottenham hotspur": "Tottenham", "spurs": "Tottenham",
    "newcastle": "Newcastle", "newcastle united": "Newcastle",
    "west ham": "West Ham", "west ham united": "West Ham",
    "wolves": "Wolves", "wolverhampton": "Wolves", "wolverhampton wanderers": "Wolves",
    "nottingham forest": "Nottingham Forest", "nott'm forest": "Nottingham Forest",
    "brighton": "Brighton", "brighton and hove albion": "Brighton",
    "leeds": "Leeds", "leeds united": "Leeds",
    "leicester": "Leicester", "leicester city": "Leicester",
    "aston villa": "Aston Villa",
    # La Liga
    "athletic bilbao": "Ath Bilbao", "athletic club": "Ath Bilbao", "athletic club bilbao": "Ath Bilbao",
    "atletico madrid": "Ath Madrid", "atletico de madrid": "Ath Madrid",
    "club atletico de madrid": "Ath Madrid",
    "real betis": "Betis", "real betis balompie": "Betis",
    "celta": "Celta", "celta de vigo": "Celta", "celta vigo": "Celta",
    "deportivo alaves": "Alaves",
    "rayo vallecano": "Vallecano",
    "espanyol": "Espanol", "rcd espanyol": "Espanol",
    "real sociedad": "Sociedad", "real oviedo": "Oviedo",
    # Serie A
    "ac milan": "Milan", "internazionale": "Inter", "inter de milao": "Inter",
    "fc internazionale milano": "Inter", "internazionale milano": "Inter",
    "as roma": "Roma", "ss lazio": "Lazio", "ssc napoli": "Napoli",
    "hellas verona": "Verona", "hellas verona fc": "Verona",
    "atalanta bc": "Atalanta", "bologna fc": "Bologna",
    "torino fc": "Torino", "udinese calcio": "Udinese",
    "us sassuolo calcio": "Sassuolo", "genoa cfc": "Genoa",
    "cagliari calcio": "Cagliari", "parma calcio": "Parma",
    "como 1907": "Como", "venezia fc": "Venezia",
    # Bundesliga
    "bayern": "Bayern Munich", "bayern munchen": "Bayern Munich",
    "fc bayern munchen": "Bayern Munich", "bayern de munique": "Bayern Munich",
    "borussia dortmund": "Dortmund",
    "borussia monchengladbach": "M'gladbach", "bor monchengladbach": "M'gladbach",
    "borussia m gladbach": "M'gladbach",
    "bayer leverkusen": "Leverkusen", "bayer 04 leverkusen": "Leverkusen",
    "vfb stuttgart": "Stuttgart",
    "1 fc union berlin": "Union Berlin",
    "1 fsv mainz 05": "Mainz", "mainz 05": "Mainz",
    "vfl wolfsburg": "Wolfsburg", "vfl bochum": "Bochum",
    "sc freiburg": "Freiburg", "sv werder bremen": "Werder Bremen",
    "fc augsburg": "Augsburg", "fc koln": "FC Cologne", "1 fc koln": "FC Cologne",
    "tsg hoffenheim": "Hoffenheim", "1899 hoffenheim": "Hoffenheim",
    "fc heidenheim": "Heidenheim", "1 fc heidenheim": "Heidenheim",
    "fc st pauli": "St Pauli", "hamburger sv": "Hamburg", "hamburg sv": "Hamburg",
    # Ligue 1
    "psg": "Paris SG", "paris saint germain": "Paris SG", "paris saint-germain": "Paris SG",
    "paris sg": "Paris SG", "paris fc": "Paris SG",
    "olympique lyonnais": "Lyon", "olympique lyon": "Lyon",
    "olympique marseille": "Marseille", "olympique de marseille": "Marseille",
    "as monaco": "Monaco", "losc lille": "Lille", "ogc nice": "Nice",
    "rc lens": "Lens", "stade rennais": "Rennes", "stade rennais fc": "Rennes",
    "stade de reims": "Reims", "fc nantes": "Nantes", "toulouse fc": "Toulouse",
    "stade brestois 29": "Brest", "rc strasbourg": "Strasbourg",
    "rc strasbourg alsace": "Strasbourg", "fc lorient": "Lorient",
    "le havre ac": "Le Havre", "aj auxerre": "Auxerre", "angers sco": "Angers",
    "as saint-etienne": "St Etienne", "saint-etienne": "St Etienne",
    "fc metz": "Metz",
    # Eredivisie
    "ajax": "Ajax", "afc ajax": "Ajax", "psv": "PSV", "psv eindhoven": "PSV",
    "feyenoord": "Feyenoord", "az alkmaar": "AZ Alkmaar", "fc utrecht": "Utrecht",
    "sc heerenveen": "Heerenveen", "fc groningen": "Groningen", "fc twente": "Twente",
    "nec nijmegen": "Nijmegen", "sparta rotterdam": "Sparta Rotterdam",
    "willem ii": "Willem II", "rkc waalwijk": "Waalwijk", "pec zwolle": "Zwolle",
    "fortuna sittard": "For Sittard", "go ahead eagles": "Go Ahead Eagles",
    "heracles almelo": "Heracles", "nac breda": "NAC Breda",
    "sc telstar": "Telstar", "sbv excelsior": "Excelsior", "fc volendam": "FC Volendam",
    # Primeira Liga
    "benfica": "Benfica", "sl benfica": "Benfica", "porto": "Porto", "fc porto": "Porto",
    "sporting": "Sp Lisbon", "sporting cp": "Sp Lisbon", "sporting lisbon": "Sp Lisbon",
    "sc braga": "Sp Braga", "sporting braga": "Sp Braga",
    "vitoria guimaraes": "Guimaraes", "vitoria de guimaraes": "Guimaraes",
    "vitoria setubal": "V Setubal", "famalicao": "Famalicao", "fc famalicao": "Famalicao",
    "estoril": "Estoril", "gd estoril praia": "Estoril", "casa pia": "Casa Pia",
    "casa pia ac": "Casa Pia", "gil vicente": "Gil Vicente", "gil vicente fc": "Gil Vicente",
    "moreirense": "Moreirense", "rio ave": "Rio Ave", "boavista": "Boavista",
    "aves": "Aves", "avs futebol sad": "AVS", "nacional": "Nacional",
    "cd nacional": "Nacional", "santa clara": "Santa Clara", "farense": "Farense",
    "sc farense": "Farense", "arouca": "Arouca", "fc arouca": "Arouca",
    "estrela": "Estrela", "estrela da amadora": "Estrela", "cd tondela": "Tondela",
    "tondela": "Tondela", "alverca": "Alverca",
    # Championship
    "middlesbrough": "Middlesbrough", "west brom": "West Brom",
    "west bromwich albion": "West Brom", "norwich": "Norwich", "norwich city": "Norwich",
    "sheffield utd": "Sheff United", "sheffield united": "Sheff United",
    "sheff utd": "Sheff United", "sheffield weds": "Sheff Weds",
    "sheffield wednesday": "Sheff Weds", "birmingham": "Birmingham",
    "birmingham city": "Birmingham", "stoke": "Stoke", "stoke city": "Stoke",
    "cardiff": "Cardiff", "cardiff city": "Cardiff", "coventry": "Coventry",
    "coventry city": "Coventry", "millwall": "Millwall", "blackburn": "Blackburn",
    "blackburn rovers": "Blackburn", "preston": "Preston", "preston north end": "Preston",
    "qpr": "QPR", "queens park rangers": "QPR", "bristol city": "Bristol City",
    "derby": "Derby", "derby county": "Derby", "swansea": "Swansea",
    "swansea city": "Swansea", "hull": "Hull", "hull city": "Hull",
    "sunderland": "Sunderland", "afc sunderland": "Sunderland",
    "watford": "Watford", "plymouth": "Plymouth", "plymouth argyle": "Plymouth",
    "portsmouth": "Portsmouth", "oxford": "Oxford", "oxford united": "Oxford",
    "luton": "Luton", "luton town": "Luton", "ipswich": "Ipswich",
    "ipswich town": "Ipswich", "southampton": "Southampton",
    "blackpool": "Blackpool", "wrexham": "Wrexham", "charlton": "Charlton",
    "charlton athletic": "Charlton",
}


def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    for ch in ".,'’-_":
        s = s.replace(ch, " ")
    parts = [p for p in s.split() if p not in ("fc", "cf", "ssc", "ac", "as", "us", "sc", "rc", "cd", "sd", "ud")]
    return " ".join(parts) if parts else " ".join(s.split())


def _f(x) -> float | None:
    try:
        v = float(str(x).strip())
        return v if v > 1.0 else None
    except (ValueError, TypeError):
        return None


def _parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()[:10]
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_fixtures_csv(text: str) -> list[dict]:
    """Converte o fixtures.csv em eventos normalizados com odds reais."""
    out = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if not row.get("Div") or not row.get("HomeTeam"):
            continue
        date = _parse_date(row.get("Date", ""))
        if date is None:
            continue
        b = lambda k: _f(row.get(k))
        ev = {
            "div": row["Div"].strip(),
            "date": date.isoformat(),
            "home": row["HomeTeam"].strip(),
            "away": (row.get("AwayTeam") or "").strip(),
            "b365": {"home": b("B365H"), "draw": b("B365D"), "away": b("B365A"),
                     "over_2.5": b("B365>2.5"), "under_2.5": b("B365<2.5")},
            "pinnacle": {"home": b("PSH"), "draw": b("PSD"), "away": b("PSA"),
                         "over_2.5": b("P>2.5"), "under_2.5": b("P<2.5")},
        }
        out.append(ev)
    return out


async def _download() -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(FIXTURES_URL, timeout=TIMEOUT,
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; FutAnalytics/2.3)"})
        if r.status_code == 200 and "HomeTeam" in r.text[:2000]:
            return r.text
    except Exception:
        pass
    return None


async def get_events(force: bool = False) -> list[dict]:
    """Eventos com odds, cacheados 12h; cai para a última cópia boa se o site cair."""
    if not force:
        cached = db.cache_get(CACHE_KEY)
        if cached is not None:
            return cached.get("events", [])
    text = await _download()
    if text is None:
        stale = db.cache_get(STALE_KEY)
        if stale is not None:
            return stale.get("events", [])
        return []
    events = parse_fixtures_csv(text)
    payload = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "events": events}
    db.cache_set(CACHE_KEY, payload, CACHE_TTL)
    db.cache_set(STALE_KEY, payload, STALE_TTL)
    return events


def feed_status(events: list[dict]) -> dict:
    leagues = sorted({e["div"] for e in events})
    return {"available": bool(events), "n_events": len(events),
            "divs": leagues,
            "books": ["Bet365", "Pinnacle"] if events else []}


def _match_team(name: str, candidates: list[str]) -> str | None:
    norm = _norm(name)
    if norm in _ALIASES and _ALIASES[norm] in candidates:
        return _ALIASES[norm]
    by_norm = {_norm(t): t for t in candidates}
    if norm in by_norm:
        return by_norm[norm]
    hits = [t for nt, t in by_norm.items() if norm and (norm in nt or nt in norm)]
    if len(hits) == 1:
        return hits[0]
    close = difflib.get_close_matches(norm, list(by_norm), n=1, cutoff=0.72)
    if close:
        return by_norm[close[0]]
    return None


def find_event(events: list[dict], div: str, home_name: str, away_name: str,
               match_date: dt.date) -> dict | None:
    """Casa o jogo do app com a linha do CSV (mesma liga, data ±1 dia, times)."""
    pool = [e for e in events if e["div"] == div and
            abs((dt.date.fromisoformat(e["date"]) - match_date).days) <= 1]
    if not pool:
        return None
    homes = sorted({e["home"] for e in pool})
    h = _match_team(home_name, homes)
    if not h:
        return None
    same_home = [e for e in pool if e["home"] == h]
    a = _match_team(away_name, sorted({e["away"] for e in same_home}))
    if not a:
        return None
    for e in same_home:
        if e["away"] == a:
            return e
    return None


# ------------------------------------------------------------ inversão Poisson
def _lambda_total_for_over(p_over: float, line: float = 2.5) -> float:
    """λ total implícito: resolve P(total >= thr) = p_over (Poisson do total)."""
    thr = int(line) + 1
    p_over = min(max(p_over, 0.02), 0.98)

    def f(T: float) -> float:
        return 1.0 - math.exp(-T) * sum(T ** k / math.factorial(k) for k in range(thr))

    lo, hi = 0.05, 7.0
    for _ in range(48):
        mid = (lo + hi) / 2
        if f(mid) < p_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def implied_lambdas(p_home: float, p_away: float, p_over25: float,
                    rho: float = -0.09) -> tuple[float, float]:
    """(λcasa, λfora) implícitos no 1X2 + Over 2.5 do consenso de mercado.

    λ total vem do Over 2.5; a razão λh/λa vem da probabilidade de vitória do
    mandante do mercado, resolvida na matriz Dixon-Coles do próprio motor.
    """
    total = _lambda_total_for_over(p_over25, 2.5)
    p_home = min(max(p_home, 0.03), 0.94)

    def p_home_given(r: float) -> float:
        lh = total * r / (1 + r)
        la = total / (1 + r)
        m = score_matrix(lh, la)
        return sum(m[i][j] for i in range(11) for j in range(11) if i > j)

    lo, hi = 0.20, 4.55
    for _ in range(40):
        mid = math.sqrt(lo * hi)
        if p_home_given(mid) < p_home:
            lo = mid
        else:
            hi = mid
    r = math.sqrt(lo * hi)
    return total * r / (1 + r), total / (1 + r)


def consensus_probs(lh: float, la: float) -> dict:
    """Todas as linhas de gols derivadas dos λ implícitos (mesma matriz do motor)."""
    mg = 10
    m = score_matrix(lh, la)

    def p_over(line: float) -> float:
        thr = int(line) + 1
        return sum(m[i][j] for i in range(mg + 1) for j in range(mg + 1) if i + j >= thr)

    p_btts = sum(m[i][j] for i in range(1, mg + 1) for j in range(1, mg + 1))
    return {
        "over_0.5": p_over(0.5), "over_1.5": p_over(1.5),
        "over_2.5": p_over(2.5), "over_3.5": p_over(3.5),
        "under_0.5": 1 - p_over(0.5), "under_1.5": 1 - p_over(1.5),
        "under_2.5": 1 - p_over(2.5), "under_3.5": 1 - p_over(3.5),
        "btts_yes": p_btts, "btts_no": 1 - p_btts,
        "ht_0.5": sum(m[i][j] for i in range(1, mg + 1) for j in range(mg + 1)),
        "at_0.5": sum(m[i][j] for i in range(mg + 1) for j in range(1, mg + 1)),
        "ht_1.5": sum(m[i][j] for i in range(2, mg + 1) for j in range(mg + 1)),
        "at_1.5": sum(m[i][j] for i in range(mg + 1) for j in range(2, mg + 1)),
        "home": sum(m[i][j] for i in range(mg + 1) for j in range(mg + 1) if i > j),
        "draw": sum(m[i][i] for i in range(mg + 1)),
        "away": sum(m[i][j] for i in range(mg + 1) for j in range(mg + 1) if i < j),
    }


def best_odds_from_event(ev: dict) -> tuple[dict, int, bool]:
    """Melhor odd REAL por mercado (max entre B365 e Pinnacle) + cobertura."""
    real: dict = {}
    books = 0
    for book in ("b365", "pinnacle"):
        ob = ev.get(book) or {}
        if any(v for v in ob.values()):
            books += 1
        for mk, v in ob.items():
            if v and v > 1.0:
                real[mk] = max(real.get(mk, 0), v)
    return real, books, bool(ev.get("pinnacle", {}).get("home"))


def build_market_odds(ev: dict, derive_margin: float = 0.06) -> dict | None:
    """Pacote completo de preços de um jogo: reais + derivadas do consenso.

    Retorna {odds, meta} ou None se o evento não tem nem 1X2.
    """
    real, books, has_pin = best_odds_from_event(ev)
    if not all(k in real for k in ("home", "draw", "away")):
        return None
    devig = de_vig_markets(real)
    ph, pa = devig.get("home"), devig.get("away")
    if ph is None or pa is None:
        return None

    probs = None
    if "over_2.5" in devig:
        lh, la = implied_lambdas(ph, pa, devig["over_2.5"])
        probs = consensus_probs(lh, la)

    margin = min(max(derive_margin, 0.0), 0.20)
    REAL_MK = {"home", "draw", "away", "over_2.5", "under_2.5"}
    odds: dict = {}
    real_list: list[str] = []
    derived_list: list[str] = []
    for mk, v in real.items():
        odds[mk] = round(v, 2)
        real_list.append(mk)
    if probs:
        for mk, p in probs.items():
            if mk in REAL_MK and mk in odds:
                continue
            if mk in odds:
                continue
            p = min(max(p, 0.02), 0.985)
            d = round((1.0 / p) * (1 - margin), 2)
            if d >= 1.03:
                odds[mk] = d
                derived_list.append(mk)
    meta = {
        "source": "football-data.co.uk",
        "books": books,
        "pinnacle": has_pin,
        "real": real_list,
        "derived": derived_list,
        "implied": [round(lh, 2), round(la, 2)] if probs else None,
    }
    return {"odds": odds, "meta": meta}


async def odds_for_match(league_name: str, home_name: str, away_name: str,
                         kickoff_iso: str, derive_margin: float = 0.06) -> dict | None:
    """Ponto de entrada: pacote de odds do jogo ou None (liga sem cobertura)."""
    div = LEAGUE_TO_DIV.get(league_name)
    if not div:
        return None
    try:
        d = dt.datetime.fromisoformat((kickoff_iso or "").replace("Z", "+00:00")).date()
    except ValueError:
        d = dt.date.today()
    events = await get_events()
    if not events:
        return None
    ev = find_event(events, div, home_name, away_name, d)
    if not ev:
        return None
    return build_market_odds(ev, derive_margin)
