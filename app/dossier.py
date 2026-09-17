"""Dossiê do time e do jogo — a parte "análise criteriosa" em código.

O modelo devolve λ e probabilidades. Este módulo devolve o **porquê**: o que
cada número significa, quais riscos existem e o que pode invalidar a leitura.

O que é calculado (tudo a partir do histórico de jogos já baixado, sem
requisição extra):

* **Calendário**: dias de descanso, jogos nos últimos 7/14/21 dias, sinais de
  congestionamento (rotação provável) ou de parada longa (ritmo perdido).
* **Recorte de mando**: rendimento só como mandante / só como visitante — o
  futebol é dois esportes diferentes por mando de campo e a média geral
  esconde isso.
* **Execução x processo (xG)**: gols − xG e xG sofrido − gols sofridos. Quem
  marca muito mais do que cria tende a regredir; quem cria e não converte
  tende a melhorar. É o sinal de "sorte" mais útil que existe em mercados de
  gols.
* **Perfil de gols**: taxa de Over 1.5/2.5/3.5, BTTS, jogos sem marcar, jogos
  sem sofrer, 0-0, média e desvio do total — o cardápio de mercados com o
  histórico real de cada time.
* **Volatilidade**: desvio-padrão do total de gols e índice de "jogo travado"
  (fração de jogos com 0 ou 1 gol). Time volátil + média alta é bom para
  linhas altas com stake menor; média alta com variância baixa é o cenário
  ideal para Over.
* **Força do calendário enfrentado** (quando o histórico traz o adversário):
  exp(média dos ratings defensivos dos adversários) — corrige a leitura de
  "média de gols" de um time que só jogou contra defesas fracas.
* **Contexto da liga**: média de gols, taxa de Over 2.5, BTTS e 0-0.
* **Bandeiras de risco e confiança**: motivos explícitos, em texto, para o
  usuário poder discordar com informação — não com fé.
"""
from __future__ import annotations

import math
import statistics
from datetime import date

# histórico de um time: (dias_atras, foi_mandante, gols_pro, gols_contra,
#                        xg_pro | None, xg_contra | None, adversario | None)


def _split(g: tuple) -> tuple:
    """Normaliza um jogo em (dias, casa, gp, gc, xgp, xgc, adv)."""
    adv = g[6] if len(g) > 6 else None
    if len(g) >= 6:
        return g[0], bool(g[1]), g[2], g[3], g[4], g[5], adv
    return g[0], bool(g[1]), g[2], g[3], None, None, adv


def _games(ts) -> list[tuple]:
    raw = getattr(ts, "games", ts) or []
    out = [_split(tuple(g)) for g in raw]
    return sorted(out, key=lambda g: g[0])  # mais recente primeiro


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _round(x, n=2):
    return None if x is None else round(x, n)


# ------------------------------------------------------------------ recortes


def rest_profile(games: list[tuple]) -> dict:
    """Descanso e carga de jogos."""
    if not games:
        return {}
    last = games[0][0]
    recent7 = sum(1 for g in games if g[0] <= 7)
    recent14 = sum(1 for g in games if g[0] <= 14)
    recent21 = sum(1 for g in games if g[0] <= 21)
    flags = []
    if last >= 21:
        flags.append("sem jogar há 3+ semanas (ritmo/forma difíceis de aferir)")
    if recent14 >= 4:
        flags.append(f"{recent14} jogos em 14 dias: rotação provável")
    if last <= 3 and recent14 >= 3:
        flags.append("pouco descanso (≤3 dias) com calendário cheio")
    return {
        "days_since_last": last,
        "games_7d": recent7,
        "games_14d": recent14,
        "games_21d": recent21,
        "flags": flags,
    }


def venue_splits(games: list[tuple], venue: str, limit: int = 20) -> dict:
    """Rendimento só naquele mando (casa ou fora)."""
    sel = [g for g in games if (g[1] == (venue == "home"))][:limit]
    if not sel:
        return {}
    n = len(sel)
    gf = [g[2] for g in sel]
    ga = [g[3] for g in sel]
    tot = [g[2] + g[3] for g in sel]
    xgf = [g[4] for g in sel if g[4] is not None]
    xga = [g[5] for g in sel if g[5] is not None]
    return {
        "n": n,
        "gf": round(_mean(gf), 2),
        "ga": round(_mean(ga), 2),
        "total": round(_mean(tot), 2),
        "over15_rate": round(sum(1 for t in tot if t >= 2) / n, 3),
        "over25_rate": round(sum(1 for t in tot if t >= 3) / n, 3),
        "btts_rate": round(sum(1 for g in sel if g[2] > 0 and g[3] > 0) / n, 3),
        "failed_to_score": round(sum(1 for f in gf if f == 0) / n, 3),
        "clean_sheets": round(sum(1 for a in ga if a == 0) / n, 3),
        "xg_for": _round(_mean(xgf), 2),
        "xg_against": _round(_mean(xga), 2),
    }


def form_profile(games: list[tuple], n: int = 5) -> dict:
    """Forma recente em pontos, com decaimento (o último jogo pesa mais)."""
    sel = games[:n]
    if not sel:
        return {}
    pts = 0.0
    weight = 0.0
    for g in sel:
        w = math.exp(-0.12 * max(g[0], 0))
        p = 3 if g[2] > g[3] else (1 if g[2] == g[3] else 0)
        pts += w * p
        weight += w
    season_pts = []
    for g in games[:10]:
        season_pts.append(3 if g[2] > g[3] else (1 if g[2] == g[3] else 0))
    return {
        "ppg_weighted": round(pts / weight, 2) if weight else None,
        "wins": sum(1 for g in sel if g[2] > g[3]),
        "draws": sum(1 for g in sel if g[2] == g[3]),
        "losses": sum(1 for g in sel if g[2] < g[3]),
        "gf": round(_mean([g[2] for g in sel]) or 0, 2),
        "ga": round(_mean([g[3] for g in sel]) or 0, 2),
        "ppg_last10": round(sum(season_pts) / len(season_pts), 2) if season_pts else None,
    }


def xg_profile(games: list[tuple], limit: int = 20) -> dict:
    """Execução vs. processo: quem está por cima ou por baixo do xG."""
    sel = [g for g in games[:limit] if g[4] is not None and g[5] is not None]
    if not sel:
        return {"available": False}
    n = len(sel)
    gf = _mean([g[2] for g in sel]) or 0.0
    ga = _mean([g[3] for g in sel]) or 0.0
    xgf = _mean([g[4] for g in sel]) or 0.0
    xga = _mean([g[5] for g in sel]) or 0.0
    diff = gf - xgf
    allow_diff = ga - xga
    notes = []
    if diff >= 0.30:
        notes.append("marca bem acima do que cria (tende a regredir)")
    elif diff <= -0.30:
        notes.append("cria mais do que converte (tende a melhorar)")
    if allow_diff <= -0.30:
        notes.append("sofre menos do que o xG sugere (defesa/goleiro por cima)")
    elif allow_diff >= 0.30:
        notes.append("sofre mais do que o xG sugere (defesa/goleiro por baixo)")
    return {
        "available": True,
        "n": n,
        "goals_for": round(gf, 2), "goals_against": round(ga, 2),
        "xg_for": round(xgf, 2), "xg_against": round(xga, 2),
        "finishing_index": round(diff, 2),
        "defensive_index": round(-allow_diff, 2),
        "notes": notes,
    }


def goals_profile(games: list[tuple], limit: int = 15) -> dict:
    """Cardápio de mercados de gols do time, com a volatilidade."""
    sel = games[:limit]
    if len(sel) < 3:
        return {}
    n = len(sel)
    tot = [g[2] + g[3] for g in sel]
    mean_tot = _mean(tot) or 0.0
    sd = statistics.pstdev(tot) if n > 1 else 0.0
    return {
        "n": n,
        "avg_total": round(mean_tot, 2),
        "sd_total": round(sd, 2),
        "median_total": statistics.median(tot),
        "over15_rate": round(sum(1 for t in tot if t >= 2) / n, 3),
        "over25_rate": round(sum(1 for t in tot if t >= 3) / n, 3),
        "over35_rate": round(sum(1 for t in tot if t >= 4) / n, 3),
        "btts_rate": round(sum(1 for g in sel if g[2] > 0 and g[3] > 0) / n, 3),
        "cagey_index": round(sum(1 for t in tot if t <= 1) / n, 3),
        "blowout_index": round(sum(1 for g in sel if abs(g[2] - g[3]) >= 3) / n, 3),
        "no_goal_rate": round(sum(1 for t in tot if t == 0) / n, 3),
    }


def schedule_strength(games: list[tuple], ratings: dict | None) -> dict:
    """Força média dos adversários enfrentados (precisa do nome do adversário).

    ``ratings`` são os ratings do modelo conjunto da liga (atk/def em log).
    Devolve índices > 1 quando o time enfrentou adversários acima da média.
    """
    if not ratings or not ratings.get("atk"):
        return {}
    atk, dfn = ratings["atk"], ratings["def"]
    opp_def, opp_atk = [], []
    for g in games[:15]:
        opp = g[6]
        if not opp:
            continue
        if opp in dfn:
            opp_def.append(dfn[opp])       # defesa do adversário quando atacamos
        if opp in atk:
            opp_atk.append(atk[opp])       # ataque do adversário quando defendemos
    if not opp_def and not opp_atk:
        return {}
    out = {"n": len(opp_def) or len(opp_atk)}
    if opp_def:
        out["faced_defense_index"] = round(math.exp(_mean(opp_def) or 0.0), 2)
    if opp_atk:
        out["faced_attack_index"] = round(math.exp(_mean(opp_atk) or 0.0), 2)
    return out


# ------------------------------------------------------------------ liga


def league_environment(matches: list[dict] | None, limit_days: int = 365) -> dict:
    """Contexto da liga a partir dos jogos com resultado (xG disponível)."""
    if not matches:
        return {}
    recent = [m for m in matches if m.get("days_ago", 0) <= limit_days] or matches
    n = len(recent)
    if not n:
        return {}
    tot = [m["hg"] + m["ag"] for m in recent]
    env = {
        "n_matches": n,
        "avg_goals": round(sum(tot) / n, 2),
        "over25_rate": round(sum(1 for t in tot if t >= 3) / n, 3),
        "btts_rate": round(sum(1 for m in recent if m["hg"] > 0 and m["ag"] > 0) / n, 3),
        "no_goal_rate": round(sum(1 for t in tot if t == 0) / n, 3),
    }
    xgs = [(m.get("xgh"), m.get("xga")) for m in recent]
    xgs = [(h, a) for h, a in xgs if h is not None and a is not None]
    if xgs:
        env["avg_xg_total"] = round(sum(h + a for h, a in xgs) / len(xgs), 2)
    return env


def percentile_rank(value: float, population: list[float]) -> float | None:
    if not population:
        return None
    below = sum(1 for x in population if x < value)
    return round(100.0 * below / len(population), 0)


def ratings_snapshot(team: str, ratings: dict | None) -> dict:
    """Posição do time na liga em ataque e defesa (percentil)."""
    if not ratings or not ratings.get("atk"):
        return {}
    atk, dfn = ratings["atk"], ratings["def"]
    out = {}
    if team in atk:
        out["attack_index"] = round(math.exp(atk[team]), 2)
        out["attack_pct"] = percentile_rank(atk[team], list(atk.values()))
    if team in dfn:
        # defesa: quanto menor o rating, melhor; invertemos para o percentil
        out["defense_index"] = round(math.exp(-dfn[team]), 2)
        out["defense_pct"] = 100 - (percentile_rank(dfn[team], list(dfn.values())) or 0)
    return out


# ------------------------------------------------------------------ jogo


def match_script(lam_home: float, lam_away: float, cagey_home: float | None,
                 cagey_away: float | None) -> dict:
    """Classifica o roteiro provável do jogo em linguagem de apostador."""
    total = lam_home + lam_away
    gap = abs(lam_home - lam_away)
    cagey = _mean([c for c in (cagey_home, cagey_away) if c is not None])
    if total >= 3.05 and min(lam_home, lam_away) >= 1.05:
        label, hint = "jogo aberto", "perfil favorece Over 2.5 e BTTS sim"
    elif total <= 2.25 or (cagey is not None and cagey >= 0.45):
        label, hint = "jogo travado", "perfil favorece Under 2.5/3.5 e Over 1.5 como âncora"
    elif gap >= 1.1:
        label, hint = "mando dominante", "perfil favorece Over 1.5 e time da casa marca; BTTS é arriscado"
    else:
        label, hint = "equilíbrio aberto", "perfil favorece Over 1.5 e ambas marcam"
    return {
        "label": label,
        "hint": hint,
        "expected_total": round(total, 2),
        "balance": round(gap, 2),
        "cagey_index": _round(cagey, 2),
    }


def build(home_games, away_games, *, lam_home: float, lam_away: float,
          ratings: dict | None = None, league: dict | None = None,
          today: date | None = None) -> dict:
    """Dossiê completo do confronto, pronto para o card e para a API."""
    gh, ga = _games(home_games), _games(away_games)
    prof_h = goals_profile(gh)
    prof_a = goals_profile(ga)
    dossier = {
        "home": {
            "rest": rest_profile(gh),
            "home_split": venue_splits(gh, "home"),
            "form": form_profile(gh),
            "xg": xg_profile(gh),
            "goals": prof_h,
            "schedule": schedule_strength(gh, ratings),
            "rating": ratings_snapshot(ratings and _name(home_games), ratings),
        },
        "away": {
            "rest": rest_profile(ga),
            "away_split": venue_splits(ga, "away"),
            "form": form_profile(ga),
            "xg": xg_profile(ga),
            "goals": prof_a,
            "schedule": schedule_strength(ga, ratings),
            "rating": ratings_snapshot(ratings and _name(away_games), ratings),
        },
        "league": league or {},
        "script": match_script(lam_home, lam_away,
                               prof_h.get("cagey_index"), prof_a.get("cagey_index")),
    }
    flags, confidence = risk_flags(dossier, len(gh), len(ga))
    dossier["risk_flags"] = flags
    dossier["confidence"] = confidence
    return dossier


def _name(ts) -> str:
    return getattr(ts, "name", "")


def risk_flags(dossier: dict, n_home: int, n_away: int) -> tuple[list[str], dict]:
    """Bandeiras de risco + nota de confiança com motivos explícitos."""
    flags: list[str] = []
    reasons: list[str] = []
    score = 1.0

    n_min = min(n_home, n_away)
    if n_min < 8:
        flags.append(f"amostra curta ({n_min} jogos): probabilidades menos confiáveis")
        score *= 0.75
        reasons.append(f"amostra curta ({n_min} jogos)")
    elif n_min < 14:
        score *= 0.92
        reasons.append(f"amostra moderada ({n_min} jogos)")

    for side in ("home", "away"):
        d = dossier[side]
        rest = d.get("rest") or {}
        for f in rest.get("flags", []):
            flags.append(f"{side}: {f}")
        xg = d.get("xg") or {}
        if not xg.get("available"):
            score *= 0.93
            reasons.append(f"{side}: sem xG (ligas fora da cobertura do Understat)")
        for note in xg.get("notes", []):
            flags.append(f"{side}: {note}")

    prof_h = dossier["home"].get("goals") or {}
    prof_a = dossier["away"].get("goals") or {}
    cageys = [p.get("cagey_index") for p in (prof_h, prof_a) if p.get("cagey_index") is not None]
    if cageys and min(cageys) >= 0.45:
        flags.append("os dois times têm histórico de jogos travados (índice de 0-1 gol alto)")

    league = dossier.get("league") or {}
    if league.get("n_matches") and league["n_matches"] < 100:
        score *= 0.95
        reasons.append("poucos jogos na liga nesta temporada")

    script = dossier.get("script") or {}
    if script.get("label") == "jogo travado":
        flags.append("roteiro travado: linhas altas de gols são risco extra")

    return flags, {"score": round(min(max(score, 0.35), 1.0), 2), "reasons": reasons}
