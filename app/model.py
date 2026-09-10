"""Motor estatístico FutAnalytics v2.

Novidades da v2 (motivadas por backtest em ~2.900 jogos reais — ver
app/backtest.py e backtest_report.md):

1. DOIS HORIZONTES DE FORÇA: força de longo prazo (janela longa, decaimento
   lento) + forma recente (poucos jogos, decaimento rápido) com peso pequeno.
   Na v1 o decaimento único de meia-vida ~12 dias deixava a amostra efetiva
   com ~4-5 jogos: o ruído dominava e o modelo não recuperava sinal.
2. SUPORTE A xG: os jogos podem trazer gols esperados (Understat). xG mede a
   qualidade das chances e é muito mais estável que gols em amostras curtas.
   O peso do xG cai automaticamente quando a cobertura é baixa.
3. CALIBRAÇÃO (app/calibration.py): as probabilidades do modelo são corrigidas
   por curvas isotônicas treinadas em temporadas passadas antes de virar EV.
4. KELLY CONSCIENTE DE INCERTEZA: o stake usa a probabilidade descontada de
   um desvio-padrão amostral, não o ponto estimado.

O resto da metodologia v1 continua: Poisson/Dixon-Coles (rho), shrinkage
bayesiano em dois níveis, devig por grupo e mistura com o consenso de mercado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------- parâmetros
# Todas as constantes ficam em PARAMS para o backtest poder fazer análise de
# sensibilidade sem tocar no código.
PARAMS = {
    "GLOBAL_HOME_AVG": 1.49,     # média de gols do mandante (âncora global)
    "GLOBAL_AWAY_AVG": 1.17,
    "GLOBAL_XG_HOME": 1.45,      # âncora global para médias de xG
    "GLOBAL_XG_AWAY": 1.15,
    "DECAY_LONG": 0.012,         # decaimento/dia da força de longo prazo (meia-vida ~58d)
    "SHRINK_LONG": 8.0,          # jogos fictícios puxando para a média (longo)
    "MAX_GAMES_LONG": 40,        # janela máxima do horizonte longo
    "DECAY_SHORT": 0.06,         # decaimento/dia da forma recente (meia-vida ~11d)
    "SHRINK_SHORT": 3.0,
    "FORM_WINDOW": 8,            # jogos do horizonte curto
    "FORM_WEIGHT": 0.25,         # peso da forma recente na força final
    "XG_WEIGHT": 0.65,           # peso do xG sobre gols brutos (quando há cobertura)
    "XG_MIN_COVERAGE": 6.0,      # jogos com xG para cobertura total
    "RHO": -0.09,                # correção Dixon-Coles
    "MAX_GOALS": 10,
    "LEAGUE_PRIOR_STRENGTH": 60.0,
    "LAMBDA_HOME_MIN": 0.15, "LAMBDA_HOME_MAX": 4.5,
    "LAMBDA_AWAY_MIN": 0.10, "LAMBDA_AWAY_MAX": 4.0,
}


def _p(name):
    return PARAMS[name]


@dataclass
class Strengths:
    atk_home: float = 1.0
    def_home: float = 1.0
    atk_away: float = 1.0
    def_away: float = 1.0
    n_eff: float = 0.0
    form: float = 0.5
    # versão baseada em xG (iguais às de cima quando não há xG)
    xg_atk_home: float = 1.0
    xg_def_home: float = 1.0
    xg_atk_away: float = 1.0
    xg_def_away: float = 1.0
    n_xg: float = 0.0


@dataclass
class TeamSample:
    """Amostra de jogos recentes de um time (mais recente primeiro).

    Cada jogo é uma tupla (dias_atras, foi_mandante, gols_pro, gols_contra)
    ou, com xG, (dias_atras, foi_mandante, gp, gc, xg_pro, xg_contra).
    xG nulo/negativo é tratado como ausente.
    """
    name: str
    games: list = field(default_factory=list)

    def _normalize(self):
        out = []
        for g in self.games:
            if len(g) >= 6:
                days, home, gf, ga, xgf, xga = g[:6]
                xgf = xgf if xgf is not None and xgf >= 0 else None
                xga = xga if xga is not None and xga >= 0 else None
                out.append((days, home, gf, ga, xgf, xga))
            else:
                days, home, gf, ga = g[:4]
                out.append((days, home, gf, ga, None, None))
        return out

    def strengths(self, home_avg: float | None = None,
                  away_avg: float | None = None,
                  xg_home_avg: float | None = None,
                  xg_away_avg: float | None = None) -> Strengths:
        P = PARAMS
        home_avg = home_avg if home_avg is not None else _p("GLOBAL_HOME_AVG")
        away_avg = away_avg if away_avg is not None else _p("GLOBAL_AWAY_AVG")
        xg_home_avg = xg_home_avg if xg_home_avg is not None else _p("GLOBAL_XG_HOME")
        xg_away_avg = xg_away_avg if xg_away_avg is not None else _p("GLOBAL_XG_AWAY")

        games = sorted(self._normalize(), key=lambda g: g[0])
        form_pts = [3 if g[2] > g[3] else (1 if g[2] == g[3] else 0) for g in games[:5]]

        def horizon(decay: float, shrink: float, max_games: int, use_xg: bool):
            wh_att = wh_def = wa_att = wa_def = 0.0
            nh = na = 0.0
            for g in games[:max_games]:
                days, home, gf, ga, xgf, xga = g
                w = math.exp(-decay * max(days, 0))
                if use_xg:
                    if xgf is None or xga is None:
                        continue
                    gf, ga = xgf, xga
                if home:
                    wh_att += w * gf; wh_def += w * ga; nh += w
                else:
                    wa_att += w * gf; wa_def += w * ga; na += w

            def shrink_avg(total, n, prior):
                return (total + prior * shrink) / (n + shrink)

            # notar o cross: ataque do mandante é normalizado pela média de
            # gols do mandante; a defesa do mandante, pela média do visitante.
            return {
                "h_att": shrink_avg(wh_att, nh, home_avg if not use_xg else xg_home_avg),
                "h_def": shrink_avg(wh_def, nh, away_avg if not use_xg else xg_away_avg),
                "a_att": shrink_avg(wa_att, na, away_avg if not use_xg else xg_away_avg),
                "a_def": shrink_avg(wa_def, na, home_avg if not use_xg else xg_home_avg),
                "n": nh + na,
            }

        # --- horizonte longo (força estrutural)
        L = horizon(_p("DECAY_LONG"), _p("SHRINK_LONG"), _p("MAX_GAMES_LONG"), use_xg=False)
        # --- horizonte curto (forma recente)
        S = horizon(_p("DECAY_SHORT"), _p("SHRINK_SHORT"), _p("FORM_WINDOW"), use_xg=False)
        # --- xG (mesma janela do longo; xG é sinal de baixa frequência)
        X = horizon(_p("DECAY_LONG"), _p("SHRINK_LONG"), _p("MAX_GAMES_LONG"), use_xg=True)

        def blend(long_k, short_k, base_avg):
            fl = long_k / base_avg
            fs = short_k / base_avg
            fw = _p("FORM_WEIGHT")
            return (1 - fw) * fl + fw * fs

        st = Strengths()
        st.atk_home = blend(L["h_att"], S["h_att"], home_avg)
        st.def_home = blend(L["h_def"], S["h_def"], away_avg)
        st.atk_away = blend(L["a_att"], S["a_att"], away_avg)
        st.def_away = blend(L["a_def"], S["a_def"], home_avg)
        st.n_eff = L["n"]
        st.form = (sum(form_pts) / (3 * len(form_pts))) if form_pts else 0.5

        # --- xG, com peso modulado pela cobertura
        if X["n"] > 0:
            st.xg_atk_home = X["h_att"] / xg_home_avg
            st.xg_def_home = X["h_def"] / xg_away_avg
            st.xg_atk_away = X["a_att"] / xg_away_avg
            st.xg_def_away = X["a_def"] / xg_home_avg
        st.n_xg = X["n"]
        return st


def league_priors(games):
    """Médias de gols casa/fora de uma liga, com shrinkage para as globais.

    games: lista de (dias_atras, foi_mandante, gols_pro, gols_contra[, xgp, xgc]).
    """
    P = PARAMS
    hg = ag = 0.0
    nh = na = 0.0
    for g in games:
        _days, is_home, gf, _ga = g[0], g[1], g[2], g[3]
        if is_home:
            hg += gf; nh += 1
        else:
            ag += gf; na += 1
    k = _p("LEAGUE_PRIOR_STRENGTH")
    home_avg = (hg + P["GLOBAL_HOME_AVG"] * k) / (nh + k)
    away_avg = (ag + P["GLOBAL_AWAY_AVG"] * k) / (na + k)
    return home_avg, away_avg


def league_xg_priors(games):
    """Médias de xG casa/fora da liga (mesma ideia dos gols)."""
    P = PARAMS
    hg = ag = 0.0
    nh = na = 0.0
    for g in games:
        if len(g) < 6 or g[4] is None or g[5] is None:
            continue
        _days, is_home, _gf, _ga, xgf, xga = g
        if is_home:
            hg += xgf; nh += 1
        else:
            ag += xga; na += 1
    k = _p("LEAGUE_PRIOR_STRENGTH")
    home_avg = (hg + P["GLOBAL_XG_HOME"] * k) / (nh + k)
    away_avg = (ag + P["GLOBAL_XG_AWAY"] * k) / (na + k)
    return home_avg, away_avg


def poisson_pmf(lmb: float, k: int) -> float:
    return math.exp(-lmb) * lmb ** k / math.factorial(k)


def dixon_coles_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def score_matrix(lh: float, la: float):
    P = PARAMS
    mg = P["MAX_GOALS"]
    m = [[0.0] * (mg + 1) for _ in range(mg + 1)]
    total = 0.0
    for i in range(mg + 1):
        for j in range(mg + 1):
            p = poisson_pmf(lh, i) * poisson_pmf(la, j) * dixon_coles_tau(i, j, lh, la, P["RHO"])
            p = max(p, 0.0)
            m[i][j] = p
            total += p
    for i in range(mg + 1):
        for j in range(mg + 1):
            m[i][j] /= total
    return m


def _blend_strength(st_h: Strengths, st_a: Strengths, xg_weight: float) -> tuple:
    """Combina força de gols com força de xG, ponderando pela cobertura de xG."""
    P = PARAMS
    cov = min(1.0, min(st_h.n_xg, st_a.n_xg) / P["XG_MIN_COVERAGE"]) if xg_weight > 0 else 0.0
    w = xg_weight * cov
    ah_atk = (1 - w) * st_h.atk_home + w * st_h.xg_atk_home
    ah_def = (1 - w) * st_h.def_home + w * st_h.xg_def_home
    aw_atk = (1 - w) * st_a.atk_away + w * st_a.xg_atk_away
    aw_def = (1 - w) * st_a.def_away + w * st_a.xg_def_away
    return ah_atk, ah_def, aw_atk, aw_def, w


def analyze_match(home: TeamSample, away: TeamSample,
                  home_avg: float | None = None, away_avg: float | None = None,
                  xg_home_avg: float | None = None, xg_away_avg: float | None = None,
                  xg_weight: float | None = None):
    P = PARAMS
    xg_weight = P["XG_WEIGHT"] if xg_weight is None else xg_weight
    home_avg = home_avg if home_avg is not None else P["GLOBAL_HOME_AVG"]
    away_avg = away_avg if away_avg is not None else P["GLOBAL_AWAY_AVG"]

    st_h = home.strengths(home_avg, away_avg, xg_home_avg, xg_away_avg)
    st_a = away.strengths(home_avg, away_avg, xg_home_avg, xg_away_avg)
    ah_atk, ah_def, aw_atk, aw_def, xg_w_eff = _blend_strength(st_h, st_a, xg_weight)

    lam_home = home_avg * ah_atk * aw_def
    lam_away = away_avg * aw_atk * ah_def
    lam_home = min(max(lam_home, P["LAMBDA_HOME_MIN"]), P["LAMBDA_HOME_MAX"])
    lam_away = min(max(lam_away, P["LAMBDA_AWAY_MIN"]), P["LAMBDA_AWAY_MAX"])

    m = score_matrix(lam_home, lam_away)
    mg = P["MAX_GOALS"]

    p_home = sum(m[i][j] for i in range(mg + 1) for j in range(mg + 1) if i > j)
    p_draw = sum(m[i][i] for i in range(mg + 1))
    p_away = 1 - p_home - p_draw

    def p_over(line: float) -> float:
        thr = int(line) + 1
        return sum(m[i][j] for i in range(mg + 1) for j in range(mg + 1) if i + j >= thr)

    p_btts = sum(m[i][j] for i in range(1, mg + 1) for j in range(1, mg + 1))

    scores = sorted(
        ((i, j, m[i][j]) for i in range(6) for j in range(6)),
        key=lambda t: -t[2],
    )[:3]

    sample_conf = min((st_h.n_eff + st_a.n_eff) / 24.0, 1.0)
    confidence = round(0.35 + 0.65 * sample_conf, 2)

    markets = {
        "over_0.5": p_over(0.5), "over_1.5": p_over(1.5),
        "over_2.5": p_over(2.5), "over_3.5": p_over(3.5),
        "under_0.5": 1 - p_over(0.5), "under_1.5": 1 - p_over(1.5),
        "under_2.5": 1 - p_over(2.5), "under_3.5": 1 - p_over(3.5),
        "btts_yes": p_btts, "btts_no": 1 - p_btts,
        "home": p_home, "draw": p_draw, "away": p_away,
        "dc_1x": p_home + p_draw, "dc_x2": p_draw + p_away, "dc_12": p_home + p_away,
    }

    return {
        "lambda_home": round(lam_home, 2),
        "lambda_away": round(lam_away, 2),
        "expected_goals_total": round(lam_home + lam_away, 2),
        "markets": {k: round(v, 4) for k, v in markets.items()},
        "fair_odds": {k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in markets.items()},
        "top_scores": [{"score": f"{i}x{j}", "prob": round(p, 4)} for i, j, p in scores],
        "form_home": round(st_h.form, 2),
        "form_away": round(st_a.form, 2),
        "sample_home": round(st_h.n_eff, 1),
        "sample_away": round(st_a.n_eff, 1),
        "confidence": confidence,
        "xg_weight_eff": round(xg_w_eff, 2),
        "xg_used": xg_w_eff > 0.05,
    }


MARKET_LABELS = {
    "over_0.5": "Mais de 0.5 gols",
    "over_1.5": "Mais de 1.5 gols",
    "over_2.5": "Mais de 2.5 gols",
    "over_3.5": "Mais de 3.5 gols",
    "under_0.5": "Menos de 0.5 gols",
    "under_1.5": "Menos de 1.5 gols",
    "under_2.5": "Menos de 2.5 gols",
    "under_3.5": "Menos de 3.5 gols",
    "btts_yes": "Ambas marcam: sim",
    "btts_no": "Ambas marcam: não",
    "home": "Vitória do mandante",
    "draw": "Empate",
    "away": "Vitória do visitante",
    "dc_1x": "Dupla chance 1X",
    "dc_x2": "Dupla chance X2",
    "dc_12": "Dupla chance 12",
}

MARKET_GROUPS = [
    ("home", "draw", "away"),
    ("over_0.5", "under_0.5"),
    ("over_1.5", "under_1.5"),
    ("over_2.5", "under_2.5"),
    ("over_3.5", "under_3.5"),
    ("btts_yes", "btts_no"),
    ("dc_1x", "dc_x2", "dc_12"),
]


def de_vig_markets(odds: dict) -> dict:
    """Probabilidade implícita nas odds com a margem da casa removida (proporcional)."""
    out = {}
    for group in MARKET_GROUPS:
        if not all(k in odds and odds[k] and odds[k] > 1.0 for k in group):
            continue
        raw = {k: 1.0 / odds[k] for k in group}
        s = sum(raw.values())
        if s <= 0:
            continue
        for k in group:
            out[k] = raw[k] / s
    return out


def blend_markets(model_markets: dict, odds: dict, model_weight: float = 0.25) -> dict:
    """Combina probabilidade do modelo (já calibrada) com o consenso do mercado."""
    w = max(0.0, min(model_weight, 1.0))
    if w >= 1.0:
        return dict(model_markets)
    market_probs = de_vig_markets(odds)
    out = dict(model_markets)
    for group in MARKET_GROUPS:
        if not all(k in model_markets and k in market_probs for k in group):
            continue
        blended = {k: w * model_markets[k] + (1 - w) * market_probs[k] for k in group}
        s = sum(blended.values())
        if s > 0:
            for k in group:
                out[k] = blended[k] / s
    return out


GOAL_MARKETS = ["over_1.5", "over_2.5", "under_2.5", "btts_yes", "btts_no", "over_3.5", "under_3.5"]
OTHER_MARKETS = ["dc_1x", "dc_x2", "home", "away"]

MARKET_FAMILY = {
    "over_1.5": "gols", "over_2.5": "gols", "over_3.5": "gols",
    "under_1.5": "gols", "under_2.5": "gols", "under_3.5": "gols",
    "btts_yes": "btts", "btts_no": "btts",
    "home": "resultado", "draw": "resultado", "away": "resultado",
    "dc_1x": "dupla", "dc_x2": "dupla", "dc_12": "dupla",
}
CANDIDATE_MARKETS = list(MARKET_FAMILY.keys())

LEG_MIN_PROB = 0.40
MAX_LEGS = 4
MAX_PER_FAMILY = 2
ANCHOR_ODD = 1.20
ANCHOR_MIN_PROB = 0.72


def candidate_markets(analysis: dict, odds: dict | None):
    probs = analysis["markets"]
    odds = odds or {}
    out = []
    for mk in CANDIDATE_MARKETS:
        p = probs.get(mk)
        if p is None or p < LEG_MIN_PROB:
            continue
        odd = odds.get(mk)
        if odd:
            ev = p * odd - 1
            score = ev
        else:
            odd = 1.0 / p
            ev = None
            score = (p - 0.5) * analysis.get("confidence", 0.5)
        out.append({
            "market": mk, "label": MARKET_LABELS[mk],
            "prob": round(p, 4), "odd": round(odd, 2),
            "ev": round(ev, 4) if ev is not None else None,
            "score": round(score, 4), "family": MARKET_FAMILY[mk],
        })
    out.sort(key=lambda c: -c["score"])
    return out


def build_multiple(analyzed: list, settings) -> dict | None:
    per_match = []
    for r in analyzed:
        cands = candidate_markets(r["analysis"], r.get("odds"))
        if cands:
            per_match.append((r, cands))
    per_match.sort(key=lambda rc: -rc[1][0]["score"])

    legs = []
    fam_count: dict[str, int] = {}
    used_markets: set = set()
    anchors = 0
    for r, cands in per_match:
        if len(legs) >= MAX_LEGS:
            break
        chosen = None
        for c in cands:
            if c["market"] in used_markets:
                continue
            if fam_count.get(c["family"], 0) >= MAX_PER_FAMILY:
                continue
            if c["odd"] < ANCHOR_ODD:
                if anchors >= 1 or c["prob"] < ANCHOR_MIN_PROB:
                    continue
            chosen = c
            break
        if chosen:
            legs.append({
                "fixture_id": r["id"],
                "match": f'{r["home"]["name"]} x {r["away"]["name"]}',
                "league": r["league"],
                "kickoff_utc": r["kickoff_utc"],
                "market": chosen["market"],
                "label": chosen["label"],
                "prob": chosen["prob"],
                "odd": chosen["odd"],
                "ev": chosen["ev"],
                "anchor": chosen["odd"] < ANCHOR_ODD,
                "_n_eff": r["analysis"].get("n_eff", 12.0),
            })
            fam_count[chosen["family"]] = fam_count.get(chosen["family"], 0) + 1
            used_markets.add(chosen["market"])
            if legs[-1]["anchor"]:
                anchors += 1

    if len(legs) < 2:
        return None

    comb_odd = 1.0
    comb_prob = 1.0
    min_n_eff = 999.0
    for l in legs:
        comb_odd *= l["odd"]
        comb_prob *= l["prob"]
        min_n_eff = min(min_n_eff, l.get("_n_eff", 12.0))

    real = all(l["ev"] is not None for l in legs)
    ev = round(comb_prob * comb_odd - 1, 4) if real else None
    stake = kelly_stake(comb_prob, comb_odd, settings.bankroll,
                        settings.kelly_fraction * 0.6,
                        settings.stake_cap_pct / 100 * 0.5,
                        n_eff=min_n_eff,
                        uncertainty=getattr(settings, "kelly_uncertainty", True)) if real else None

    return {
        "legs": legs,
        "combined_odd": round(comb_odd, 2),
        "combined_prob": round(comb_prob, 4),
        "ev": ev,
        "stake": stake,
    }


def pick_best_market(analysis: dict, odds: dict | None):
    """
    Escolhe o melhor mercado do jogo.
    Com odds reais: maior EV positivo (prioridade a mercados de gols).
    Sem odds: mercado de gols com prob entre 0.60 e 0.90 mais distante do coin flip.
    """
    probs = analysis["markets"]
    candidates = []
    for mk in GOAL_MARKETS + OTHER_MARKETS:
        p = probs.get(mk, 0)
        if not (0.52 <= p <= 0.92):
            continue
        goal_bonus = 0.03 if mk in GOAL_MARKETS else 0.0
        odd = (odds or {}).get(mk)
        if odd:
            ev = p * odd - 1
            score = ev + goal_bonus
            candidates.append((score, mk, p, odd, ev))
        else:
            fair = 1 / p
            score = (p - 0.5) * analysis["confidence"] + goal_bonus
            candidates.append((score, mk, p, fair, None))
    if not candidates:
        return None
    candidates.sort(key=lambda t: -t[0])
    score, mk, p, odd, ev = candidates[0]
    return {
        "market": mk,
        "label": MARKET_LABELS[mk],
        "prob": round(p, 4),
        "odd": round(odd, 2),
        "odd_is_fair": ev is None,
        "ev": round(ev, 4) if ev is not None else None,
        "score": round(score, 4),
    }


def kelly_stake(prob: float, odd: float, bankroll: float,
                fraction: float = 0.25, cap_pct: float = 0.03,
                n_eff: float | None = None, uncertainty: bool = True) -> dict:
    """Kelly fracionado com teto de risco por aposta.

    v2: com uncertainty=True, a probabilidade usada no Kelly é a estimativa
    descontada de um desvio-padrão amostral (p - sigma), com sigma =
    sqrt(p(1-p)/n). Amostra pequena -> sigma grande -> stake menor.
    """
    b = odd - 1
    if b <= 0:
        return {"stake": 0.0, "kelly_full": 0.0, "pct": 0.0}
    p = prob
    sigma = 0.0
    if uncertainty and n_eff:
        n = max(n_eff * 2.0, 8.0)   # cada jogo informa ~2 observações (pró e contra)
        sigma = math.sqrt(max(p * (1 - p), 1e-9) / n)
        p = max(p - sigma, 0.5 * prob)
    q = 1 - p
    k = (b * p - q) / b
    k = max(k, 0.0)
    frac = min(k * fraction, cap_pct)
    return {
        "stake": round(bankroll * frac, 2),
        "kelly_full": round(k, 4),
        "pct": round(frac * 100, 2),
        "sigma": round(sigma, 4),
    }
