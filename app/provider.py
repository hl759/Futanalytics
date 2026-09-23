"""
Camada de dados: busca jogos do dia e histórico recente dos times.

Provedores suportados:
- football-data.org (v4): tier gratuito cobre Brasileirão Série A, Premier League,
  La Liga, Serie A, Bundesliga, Ligue 1, Champions League e mais. Limite 10 req/min.
- API-Football (api-sports.io v3): tier gratuito 100 req/dia; cobre odds reais.
- OpenLigaDB: grátis, sem chave, sem cota — fallback automático (Bundesliga + outras)
- ESPN (não-oficial): grátis, sem chave, cobre Brasileirão, Libertadores, Champions
- demo: dados simulados realistas para testar a plataforma sem chave.

Fallback automático (v2.4):
  fd --(falha/vazio)--> openliga --(falha)--> espn --(falha)--> demo
  Garante que o painel nunca fique vazio por instabilidade de uma API só.

Tudo é cacheado em SQLite para economizar requisições.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import random
import time
from datetime import date, timedelta

import httpx

from . import db

FD_BASE = "https://api.football-data.org/v4"
AF_BASE = "https://v3.football.football-api-sports.io"
AF_BASE_ALT = "https://v3.football.api-sports.io"
OPENLIGA_BASE = "https://www.openligadb.de/api"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

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

# OpenLigaDB: shortcuts -> nome interno (mesmo nome usado no resto do app)
# Fonte: https://www.openligadb.de/api/getavailableleagues
OPENLIGA_LEAGUES = {
    "bl1": "Bundesliga",
    "bl2": "Championship (Inglaterra)",  # 2. Bundesliga - mapeado como segunda divisão para não perder jogos
    "bl3": "Bundesliga",  # 3. Liga - fallback
    "dfb": "Bundesliga",
    "cl": "Champions League",
    "el": "Champions League",  # Europa League -> cai no mesmo bucket
    "em": "Champions League",
    "wm": "Champions League",
}

# OpenLigaDB shortcuts que vamos consultar por dia (custo zero, sem cota)
OPENLIGA_SHORTCUTS = ["bl1", "bl2", "cl"]

# ESPN: código da liga na ESPN -> nome interno (apenas principais, sem secundárias)
# v2.4.3: removidas MLS, Liga MX, Argentine, Nations, amistosos — experiência ruim de análise
ESPN_LEAGUES = {
    "bra.1": "Brasileirão Série A",
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ita.1": "Serie A (Itália)",
    "ger.1": "Bundesliga",
    "fra.1": "Ligue 1",
    "por.1": "Primeira Liga (Portugal)",
    "ned.1": "Eredivisie",
    "uefa.champions": "Champions League",
    "conmebol.libertadores": "Libertadores",
    "conmebol.sudamericana": "Libertadores",
    "bra.copa": "Copa do Brasil",
}

ESPN_CODES = ["bra.1", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "por.1", "ned.1", "uefa.champions", "conmebol.libertadores"]


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------- football-data
# Tier gratuito: 10 req/min. Em vez de estourar a cota em rajada e virar uma
# enxurrada de 429 + esperas de 1 min (que parecia "o app travado"), as
# chamadas são espaçadas de forma a caber no orçamento sempre.
_FD_MIN_INTERVAL = 6.2          # segundos entre chamadas (~9,7 req/min)
_fd_last_call = 0.0


async def _fd_pace():
    global _fd_last_call
    now = time.monotonic()
    wait = _fd_last_call + _FD_MIN_INTERVAL - now
    if wait > 0:
        await asyncio.sleep(wait)
    _fd_last_call = time.monotonic()


def _retry_after_seconds(headers) -> float:
    try:
        return max(1.0, float(headers.get("Retry-After", "15")))
    except (TypeError, ValueError):
        return 15.0


async def _fd_get(client: httpx.AsyncClient, path: str, token: str, params=None):
    """GET com timeout/rate-limit tratados: TODO erro vira ProviderError.

    Antes, um ConnectError/ReadTimeout/HTTP 5xx escapava sem tratamento e
    derrubava o /api/day INTEIRO (500) — nenhuma análise aparecia.
    """
    last_err: Exception | None = None
    for attempt in range(4):
        await _fd_pace()
        try:
            r = await client.get(
                f"{FD_BASE}{path}",
                headers={"X-Auth-Token": token},
                params=params or {},
                timeout=25,
            )
        except httpx.HTTPError as e:
            last_err = e
            await asyncio.sleep(1.5)
            continue
        if r.status_code == 429:
            await asyncio.sleep(min(_retry_after_seconds(r.headers) + 1, 35))
            continue
        if r.status_code in (400, 403):
            raise ProviderError(f"football-data.org recusou ({r.status_code}): {r.text[:200]}")
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            last_err = e
            await asyncio.sleep(1.5)
            continue
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError(f"football-data.org devolveu resposta inválida: {e}")
    if last_err is not None:
        raise ProviderError(f"Falha de conexão com a football-data.org: {last_err}")
    raise ProviderError("Limite de requisições da football-data.org excedido; tente em 1 minuto.")


BR_TZ = dt.timezone(dt.timedelta(hours=-3))  # America/Sao_Paulo


def _local_day(utc_iso: str) -> str:
    """Converte kickoff UTC para a data local do Brasil (UTC-3).

    Jogos FUTUROS sem horário fechado pela liga chegam da API marcados
    "T00:00:00Z" (hora placeholder). Converter 00:00 UTC para UTC-3 jogava o
    jogo para a VÉSPERA — o jogo de sábado aparecia na sexta (ou "sumia" do
    dia certo). Hora placeholder fica na data oficial que a própria API marca.
    Jogos reais a 00:00 UTC praticamente não existem nas ligas monitoradas.
    """
    d = dt.datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    if (d.hour, d.minute, d.second) == (0, 0, 0):
        return str(d.date())
    return str(d.astimezone(BR_TZ).date())


def _fd_collect(matches: list, day: str) -> tuple[list, dict, int]:
    """Filtra as partidas da API: (jogos do dia pedido, contagem por dia, nº na janela)."""
    out = []
    counts: dict[str, int] = {}
    monitored = 0
    for m in matches or []:
        try:
            code = m.get("competition", {}).get("code")
            if code not in FD_COMPETITIONS:
                continue
            utc = m.get("utcDate")
            if not utc:
                continue
            monitored += 1
            local_d = _local_day(utc)
            counts[local_d] = counts.get(local_d, 0) + 1
            if local_d != day:
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
        except (KeyError, TypeError, ValueError):
            continue  # jogo mal formado na API: pula, não derruba o dia
    return out, counts, monitored


async def _fd_fixtures_by_competition(client: httpx.AsyncClient, token: str,
                                      d0: dt.date, day: str) -> tuple[list, dict]:
    """Plano B: busca /competitions/{code}/matches, UMA liga por vez.

    Acionado quando a resposta global /matches veio estranhamente vazia (a API
    devolve HTTP 200 com lista vazia em alguns problemas de permissão/plano —
    sem erro — e o painel mostrava "dia sem jogos" em TODOS os dias futuros,
    enquanto os passados pareciam funcionar por estarem cacheados 7 dias).
    O endpoint por competição devolve 403 explícito nesses casos, que o app
    transforma em mensagem clara em vez de silêncio.
    """
    out: list = []
    counts: dict[str, int] = {}
    for code in FD_COMPETITIONS:
        try:
            data = await _fd_get(
                client, f"/competitions/{code}/matches", token,
                {"dateFrom": str(d0 - timedelta(days=4)),
                 "dateTo": str(d0 + timedelta(days=5))},
            )
        except ProviderError:
            raise  # 403/bloqueio de plano tem que virar mensagem visível
        fx, cnt, _m = _fd_collect(data.get("matches", []), day)
        out.extend(fx)
        for d, n in cnt.items():
            counts[d] = counts.get(d, 0) + n
    return out, counts


async def fd_fixtures(token: str, day: str):
    key = f"fd:fixtures:v4:{day}"
    cached = db.cache_get(key)
    if cached is not None:
        return cached
    # Janela ampla ao redor do dia: (1) o dateTo da API é EXCLUSIVO (não inclui
    # o próprio dia — por isso o +5 para cobrir ±4 dias de verdade); (2) jogos
    # noturnos no Brasil caem no dia seguinte em UTC; (3) MESMO CUSTO DE COTA
    # (1 requisição) e o painel ganha a contagem de jogos de todos os dias da
    # semana — dia sem rodada deixa de ser um beco sem saída: mostramos QUANDO
    # os próximos jogos acontecem.
    try:
        d0 = dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ProviderError(f"Data inválida: {day!r}. Use o formato AAAA-MM-DD.")
    dbg = {"endpoint": "/matches",
           "window": f"{d0 - timedelta(days=4)} a {d0 + timedelta(days=4)}",
           "fallback": False}
    async with httpx.AsyncClient() as client:
        data = await _fd_get(
            client, "/matches", token,
            {"dateFrom": str(d0 - timedelta(days=4)),
             "dateTo": str(d0 + timedelta(days=5)),  # dateTo é EXCLUSIVO (doc v4)
             "limit": 500},
        )
        matches = data.get("matches", []) or []
        res_count = ((data.get("resultSet") or {}).get("count"))
        out, counts, monitored = _fd_collect(matches, day)
        dbg.update(api_matches=len(matches), api_reported_count=res_count,
                   monitored_matches=monitored, day_matches=len(out))
        # Resposta vazia numa janela de 9 dias com ~10 ligas ativas é quase
        # impossível de ser verdade — muito mais provável ser permissão/plano
        # resolvido em silêncio pela API (ou truncamento: resultSet.count maior
        # que o número de jogos devolvidos). Plano B: por competição.
        truncated = isinstance(res_count, int) and res_count > len(matches)
        if not out and (not matches or truncated):
            dbg["fallback"] = True
            dbg["fallback_reason"] = ("truncada" if truncated else "vazia")
            out, counts = await _fd_fixtures_by_competition(client, token, d0, day)
            dbg["day_matches"] = len(out)
    # Resultado vazio (dia sem rodada genuíno) expira rápido: se a agenda muda
    # (jogo remarcado/criado), o painel percebe em minutos, não em horas.
    if out:
        ttl = 3600 if day >= str(date.today()) else 7 * 86400
    else:
        ttl = 600
    db.cache_set(key, out, ttl)
    # Contagem de jogos por dia da JANELA (mesma resposta da API: custo zero em
    # cota). Dias sem rodada nas ligas monitoradas (ex.: segunda-feira) não são
    # defeito — o painel usa estas contagens para mostrar quando voltam a haver
    # jogos em vez de um "nenhum jogo" que parece quebra.
    db.cache_set(f"fd:daycounts:v1:{day}", counts, ttl)
    db.cache_set(f"fd:debug:v1:{day}", dbg, ttl)
    return out


def fd_day_counts(day: str) -> dict:
    """Jogos por dia na janela já baixada do dia pedido (só cache; não gasta cota)."""
    return db.cache_get(f"fd:daycounts:v1:{day}") or {}


def fd_day_debug(day: str) -> dict:
    """Diagnóstico de etapas do dia pedido (só cache; alimenta a tela vazia)."""
    return db.cache_get(f"fd:debug:v1:{day}") or {}


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
    for m in sorted(data.get("matches", []), key=lambda x: x.get("utcDate") or "", reverse=True)[:18]:
        try:
            ft = m.get("score", {}).get("fullTime", {})
            hg, ag = ft.get("home"), ft.get("away")
            if hg is None or ag is None:
                continue
            played = dt.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).date()
            days_ago = (today - played).days
            is_home = m["homeTeam"]["id"] == team_id
            gf, ga = (hg, ag) if is_home else (ag, hg)
            games.append([days_ago, is_home, gf, ga])
        except (KeyError, TypeError, ValueError):
            continue  # linha corrompida não derruba o histórico do time
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
        try:
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
        except (KeyError, TypeError, ValueError):
            continue
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
        try:
            goals = f.get("goals", {})
            hg, ag = goals.get("home"), goals.get("away")
            if hg is None or ag is None:
                continue
            played = dt.datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")).date()
            days_ago = (today - played).days
            is_home = f["teams"]["home"]["id"] == team_id
            gf, ga = (hg, ag) if is_home else (ag, hg)
            games.append([days_ago, is_home, gf, ga])
        except (KeyError, TypeError, ValueError):
            continue
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
    except (IndexError, KeyError, TypeError):
        bms = []
    for bm in bms[:3]:
        for bet in bm.get("bets", []):
            name = bet.get("name", "")
            for v in bet.get("values", []):
                try:
                    val, odd = str(v.get("value")), float(v.get("odd", 0) or 0)
                except (TypeError, ValueError):
                    continue
                mk = None
                if name == "Match Winner":
                    mk = {"Home": "home", "Draw": "draw", "Away": "away"}.get(val)
                elif name == "Goals Over/Under":
                    if val == "Over 0.5": mk = "over_0.5"
                    elif val == "Over 1.5": mk = "over_1.5"
                    elif val == "Over 2.5": mk = "over_2.5"
                    elif val == "Over 3.5": mk = "over_3.5"
                    elif val == "Under 0.5": mk = "under_0.5"
                    elif val == "Under 1.5": mk = "under_1.5"
                    elif val == "Under 2.5": mk = "under_2.5"
                    elif val == "Under 3.5": mk = "under_3.5"
                elif name == "Both Teams Score":
                    mk = {"Yes": "btts_yes", "No": "btts_no"}.get(val)
                elif name in ("Home Over/Under", "Home Team Total"):
                    mk = {"Over 0.5": "ht_0.5", "Over 1.5": "ht_1.5"}.get(val)
                elif name in ("Away Over/Under", "Away Team Total"):
                    mk = {"Over 0.5": "at_0.5", "Over 1.5": "at_1.5"}.get(val)
                elif name == "Double Chance":
                    mk = {"Home/Draw": "dc_1x", "Draw/Away": "dc_x2", "Home/Away": "dc_12"}.get(val)
                if mk and odd > 1.0:
                    odds[mk] = max(odds.get(mk, 0), odd)  # melhor odd entre casas
    db.cache_set(ck, odds, 2 * 3600)
    return odds


# -------------------------------------------------------------- fallback: OpenLigaDB (sem chave, sem cota)
# Docs: https://www.openligadb.de/api/getmatchdata/{leagueShortcut}/{leagueSeason}
# Exemplo: /api/getmatchdata/bl1/2025  -> todos os jogos da Bundesliga 2025/26
# Cada jogo tem matchDateTimeUTC, team1/team2, matchID etc.

def _ol_current_season(d: dt.date) -> int:
    # temporada europeia: 2025 = 2025/26, começa em julho
    return d.year if d.month >= 7 else d.year - 1

async def _ol_get(client: httpx.AsyncClient, path: str):
    try:
        r = await client.get(f"{OPENLIGA_BASE}{path}", timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (FutAnalytics fallback)"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise ProviderError(f"OpenLigaDB falhou: {e}")

async def openliga_fixtures(day: str) -> list:
    """Busca jogos do dia no OpenLigaDB (grátis, sem chave)."""
    ck = f"ol:fixtures:v2:{day}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    try:
        d0 = dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ProviderError(f"Data inválida: {day}")
    seasons = [_ol_current_season(d0), _ol_current_season(d0) - 1]
    out = []
    success = False
    errors = 0
    async with httpx.AsyncClient() as client:
        for season in seasons:
            for shortcut in OPENLIGA_SHORTCUTS:
                try:
                    data = await _ol_get(client, f"/getmatchdata/{shortcut}/{season}")
                    success = True
                except ProviderError:
                    errors += 1
                    continue
                if not isinstance(data, list):
                    continue
                for m in data:
                    try:
                        utc_str = m.get("matchDateTimeUTC") or m.get("matchDateTime")
                        if not utc_str:
                            continue
                        if not utc_str.endswith("Z") and "T" in utc_str:
                            utc_iso = utc_str + "Z" if "+" not in utc_str else utc_str
                        else:
                            utc_iso = utc_str
                        try:
                            local_d = _local_day(utc_iso)
                        except Exception:
                            local_d = utc_iso[:10]
                        if local_d != day:
                            continue
                        t1 = m.get("team1") or {}
                        t2 = m.get("team2") or {}
                        t1_name = t1.get("teamName") or t1.get("shortName") or "Time A"
                        t2_name = t2.get("teamName") or t2.get("shortName") or "Time B"
                        league_name = OPENLIGA_LEAGUES.get(shortcut, f"Liga {shortcut}")
                        mid = m.get("matchID") or f"{shortcut}-{t1.get('teamId')}-{t2.get('teamId')}"
                        out.append({
                            "id": f"ol-{mid}",
                            "provider": "openliga",
                            "league": league_name,
                            "kickoff_utc": utc_iso,
                            "status": "SCHEDULED" if m.get("matchIsFinished") is False else "FINISHED" if m.get("matchIsFinished") else "SCHEDULED",
                            "home": {"id": t1.get("teamId", 0), "name": t1_name},
                            "away": {"id": t2.get("teamId", 0), "name": t2_name},
                            "_ol_shortcut": shortcut,
                            "_ol_season": season,
                        })
                    except (KeyError, TypeError, ValueError):
                        continue
            if out:
                break  # já achou jogos, não precisa varrer temporada anterior
    if not success and errors > 0:
        raise ProviderError(f"OpenLigaDB fora do ar (tentou {errors} ligas)")
    ttl = 3600 if day >= str(date.today()) else 6 * 3600
    db.cache_set(ck, out, ttl)
    return out

async def openliga_team_recent(team_id: int, team_name: str) -> list:
    """Histórico recente via OpenLigaDB: varre temporadas atual e anterior."""
    ck = f"ol:team:{team_id}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    today = date.today()
    seasons = [_ol_current_season(today), _ol_current_season(today) - 1]
    games = []
    async with httpx.AsyncClient() as client:
        for season in seasons:
            for shortcut in OPENLIGA_SHORTCUTS:
                try:
                    data = await _ol_get(client, f"/getmatchdata/{shortcut}/{season}")
                except ProviderError:
                    continue
                if not isinstance(data, list):
                    continue
                for m in data:
                    try:
                        if not m.get("matchIsFinished"):
                            continue
                        t1 = m.get("team1") or {}
                        t2 = m.get("team2") or {}
                        if t1.get("teamId") != team_id and t2.get("teamId") != team_id:
                            continue
                        # resultado final
                        res = None
                        for r in (m.get("matchResults") or []):
                            if r.get("resultTypeID") == 2:  # Endergebnis
                                res = r
                                break
                        if not res:
                            # pega último resultado disponível
                            results = m.get("matchResults") or []
                            if results:
                                res = results[-1]
                        if not res:
                            continue
                        hg = res.get("pointsTeam1")
                        ag = res.get("pointsTeam2")
                        if hg is None or ag is None:
                            continue
                        dt_str = m.get("matchDateTimeUTC") or m.get("matchDateTime") or ""
                        try:
                            played = dt.datetime.fromisoformat(dt_str.replace("Z", "+00:00")).date()
                        except Exception:
                            continue
                        days_ago = (today - played).days
                        if days_ago < 0 or days_ago > 240:
                            continue
                        is_home = t1.get("teamId") == team_id
                        gf, ga = (hg, ag) if is_home else (ag, hg)
                        games.append([days_ago, is_home, gf, ga])
                    except (KeyError, TypeError, ValueError):
                        continue
    # ordena por mais recente e limita
    games = sorted(games, key=lambda x: x[0])[:20]
    db.cache_set(ck, games, 12 * 3600)
    return games


# -------------------------------------------------------------- fallback: ESPN (sem chave, cobre Brasileirão)
async def _espn_get(client: httpx.AsyncClient, path: str, params=None):
    try:
        r = await client.get(f"{ESPN_BASE}{path}", params=params or {}, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (FutAnalytics fallback)"})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise ProviderError(f"ESPN falhou: {e}")

async def espn_fixtures(day: str) -> list:
    """Busca jogos do dia na ESPN (grátis, sem chave). Apenas ligas principais."""
    ck = f"espn:fixtures:v2:{day}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    try:
        d0 = dt.date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ProviderError(f"Data inválida: {day}")
    espn_date = d0.strftime("%Y%m%d")
    out = []
    success = False
    errors = 0
    async with httpx.AsyncClient() as client:
        for code in ESPN_CODES:
            try:
                data = await _espn_get(client, f"/{code}/scoreboard", {"dates": espn_date})
                success = True
            except ProviderError:
                errors += 1
                continue
            events = data.get("events") or []
            for ev in events:
                try:
                    comps = ev.get("competitions") or []
                    if not comps:
                        continue
                    comp = comps[0]
                    competitors = comp.get("competitors") or []
                    if len(competitors) < 2:
                        continue
                    home_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
                    away_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1] if len(competitors) > 1 else competitors[0])
                    home_team = home_c.get("team") or {}
                    away_team = away_c.get("team") or {}
                    home_name = home_team.get("displayName") or home_team.get("name") or "Casa"
                    away_name = away_team.get("displayName") or away_team.get("name") or "Fora"
                    utc_iso = ev.get("date") or comp.get("date") or ""
                    if not utc_iso:
                        continue
                    try:
                        local_d = _local_day(utc_iso)
                    except Exception:
                        local_d = utc_iso[:10]
                    if local_d != day:
                        continue
                    league_name = ESPN_LEAGUES.get(code, code)
                    out.append({
                        "id": f"espn-{ev.get('id')}",
                        "provider": "espn",
                        "league": league_name,
                        "kickoff_utc": utc_iso,
                        "status": comp.get("status", {}).get("type", {}).get("name", "SCHEDULED"),
                        "home": {"id": int(home_team.get("id", 0) or 0), "name": home_name},
                        "away": {"id": int(away_team.get("id", 0) or 0), "name": away_name},
                        "_espn_league": code,
                    })
                except (KeyError, TypeError, ValueError):
                    continue
    if not success and errors > 0:
        raise ProviderError(f"ESPN fora do ar (tentou {errors} ligas)")
    ttl = 3600 if day >= str(date.today()) else 6 * 3600
    db.cache_set(ck, out, ttl)
    return out

async def espn_team_recent(team_id: int, team_name: str) -> list:
    """Histórico recente via ESPN: tenta buscar schedule do time nas ligas principais."""
    ck = f"espn:team:{team_id}"
    cached = db.cache_get(ck)
    if cached is not None:
        return cached
    today = date.today()
    games = []
    # Para simplificar, varre as ligas principais buscando o time por nome
    # Se não achar, retorna vazio e o modelo usa só médias da liga + xG (se houver)
    async with httpx.AsyncClient() as client:
        for code in ESPN_CODES[:6]:  # só as principais para não estourar
            try:
                # endpoint de schedule: /{league}/teams/{teamId}/schedule
                # team_id da ESPN é diferente, então tentamos buscar por nome via scoreboard histórico
                # fallback: busca últimos 30 dias de scoreboard e filtra por nome
                for delta in range(0, 60, 7):
                    d = today - timedelta(days=delta)
                    espn_date = d.strftime("%Y%m%d")
                    try:
                        data = await _espn_get(client, f"/{code}/scoreboard", {"dates": espn_date})
                    except ProviderError:
                        continue
                    for ev in data.get("events") or []:
                        try:
                            comps = ev.get("competitions") or []
                            if not comps:
                                continue
                            comp = comps[0]
                            # só jogos finalizados
                            if comp.get("status", {}).get("type", {}).get("completed") is not True:
                                continue
                            competitors = comp.get("competitors") or []
                            # verifica se nosso time está no jogo (por nome aproximado)
                            found = False
                            is_home = False
                            gf = ga = None
                            for c in competitors:
                                t = c.get("team") or {}
                                name = (t.get("displayName") or "").lower()
                                if team_name.lower() in name or name in team_name.lower():
                                    found = True
                                    is_home = c.get("homeAway") == "home"
                                    # score
                                    try:
                                        gf = int(c.get("score", 0))
                                    except (TypeError, ValueError):
                                        gf = None
                            if not found:
                                continue
                            # pega placar adversário
                            for c in competitors:
                                if (c.get("homeAway") == "home") != is_home:
                                    try:
                                        ga = int(c.get("score", 0))
                                    except (TypeError, ValueError):
                                        ga = None
                            if gf is None or ga is None:
                                continue
                            played = dt.datetime.fromisoformat((ev.get("date") or "").replace("Z", "+00:00")).date()
                            days_ago = (today - played).days
                            if days_ago < 0 or days_ago > 240:
                                continue
                            games.append([days_ago, is_home, gf, ga])
                        except (KeyError, TypeError, ValueError):
                            continue
                    if len(games) >= 15:
                        break
            except Exception:
                continue
            if len(games) >= 15:
                break
    games = sorted(games, key=lambda x: x[0])[:20]
    db.cache_set(ck, games, 12 * 3600)
    return games


# Função de fallback automático: tenta fd -> openliga -> espn -> (vazio, não demo)
# v2.4.1: corrige bug que fazia cair no demo em dia sem rodada genuíno.
# Demo só é usado quando o usuário escolhe demo explicitamente ou quando TODAS
# as fontes reais falham por erro de rede/token. Dia sem jogo mostra "Nenhum jogo"
# com chips dos próximos dias, não jogos fake tipo "Arsenal x Fortaleza".
async def fixtures_with_fallback(primary: str, day: str, fd_token: str = "", af_key: str = "") -> tuple[list, str, dict]:
    """
    Retorna (fixtures, provider_usado, info_fallback)
    info_fallback: {tried: [...], used: str, fallback: bool, reason: str}
    """
    tried = []
    last_error = None

    # 1) primário: fd
    if primary == "fd":
        if not fd_token:
            # sem token, não tenta fd — vai direto para fallbacks gratuitos
            tried.append("fd: no-token")
        else:
            tried.append("fd")
            try:
                fx = await fd_fixtures(fd_token, day)
                # fd_fixtures sempre retorna lista (pode ser vazia = dia sem rodada)
                # Verifica se é falha impossível (0 jogos em 9 dias) via debug
                if not fx:
                    dbg = fd_day_debug(day)
                    counts = fd_day_counts(day)
                    api_matches = dbg.get("api_matches", -1)
                    monitored = dbg.get("monitored_matches", -1)
                    # 0 absoluto numa janela de 9 dias com ~10 ligas = falha, não calendário
                    if api_matches == 0 or (api_matches == -1 and not counts):
                        last_error = f"fd retornou 0 jogos na janela (api_matches=0) — falha"
                        # continua para fallback
                    else:
                        # dia sem rodada genuíno: retorna vazio, sem fallback para demo
                        return fx, "fd", {"tried": tried, "used": "fd", "fallback": False, "reason": "dia sem rodada"}
                else:
                    return fx, "fd", {"tried": tried, "used": "fd", "fallback": False}
            except ProviderError as e:
                last_error = str(e)
                # continua para fallback

    # 1b) primário: af
    elif primary == "af":
        if not af_key:
            tried.append("af: no-key")
        else:
            tried.append("af")
            try:
                fx = await af_fixtures(af_key, day)
                if fx:
                    return fx, "af", {"tried": tried, "used": "af", "fallback": False}
                else:
                    # af retornou vazio: pode ser dia sem rodada, retorna vazio
                    return fx, "af", {"tried": tried, "used": "af", "fallback": False, "reason": "dia sem rodada"}
            except ProviderError as e:
                last_error = str(e)

    elif primary == "demo":
        tried.append("demo")
        return demo_fixtures(day), "demo", {"tried": tried, "used": "demo", "fallback": False}

    # 2) fallback OpenLigaDB (sem chave, sem cota)
    tried.append("openliga")
    try:
        fx = await openliga_fixtures(day)
        if fx:
            return fx, "openliga", {"tried": tried, "used": "openliga", "fallback": True, "reason": last_error or "fd vazio, usando openliga"}
        # se openliga retornou vazio genuíno, tenta ESPN
    except ProviderError as e:
        last_error = str(e)

    # 3) fallback ESPN (sem chave, cobre Brasileirão)
    tried.append("espn")
    try:
        fx = await espn_fixtures(day)
        if fx:
            return fx, "espn", {"tried": tried, "used": "espn", "fallback": True, "reason": last_error or "fd/openliga vazios, usando espn"}
    except ProviderError as e:
        last_error = str(e)

    # 4) Se chegou aqui, todas as fontes reais falharam por erro OU retornaram vazio genuíno
    # Se foi erro, último recurso é demo (para nunca quebrar). Se foi vazio genuíno, retorna vazio.
    # Distingue pelos tried: se tentamos fd e ele tinha day_counts ou debug com jogos, é vazio genuíno.
    if primary == "fd" and fd_token:
        dbg = fd_day_debug(day)
        counts = fd_day_counts(day)
        if dbg.get("api_matches", 0) > 0 or counts:
            # dia sem rodada genuíno nas ligas monitoradas
            return [], "fd", {"tried": tried, "used": "fd", "fallback": False, "reason": "dia sem rodada, sem fallback demo"}

    # Todas falharam por erro de rede/token: último recurso demo (antes mostrava fake sem aviso, agora com aviso claro)
    tried.append("demo")
    return demo_fixtures(day), "demo", {"tried": tried, "used": "demo", "fallback": True, "reason": last_error or "todas as fontes reais falharam"}


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
