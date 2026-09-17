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
                    wh_att += w * gf
                    wh_def += w * ga
                    nh += w
                else:
                    wa_att += w * gf
                    wa_def += w * ga
                    na += w

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
            hg += gf
            nh += 1
        else:
            ag += gf
            na += 1
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
            hg += xgf
            nh += 1
        else:
            ag += xga
            na += 1
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
                  xg_weight: float | None = None,
                  lam_override: tuple | None = None):
    P = PARAMS
    xg_weight = P["XG_WEIGHT"] if xg_weight is None else xg_weight
    home_avg = home_avg if home_avg is not None else P["GLOBAL_HOME_AVG"]
    away_avg = away_avg if away_avg is not None else P["GLOBAL_AWAY_AVG"]

    st_h = home.strengths(home_avg, away_avg, xg_home_avg, xg_away_avg)
    st_a = away.strengths(home_avg, away_avg, xg_home_avg, xg_away_avg)

    if lam_override is not None:
        # λ do modelo conjunto (ajustado por adversário no nível da liga)
        lam_home, lam_away = lam_override
        xg_w_eff = 1.0
    else:
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

    # v2.2 — análise de gols por time (totais individuais, da margem da matriz)
    p_home_scores = 1.0 - sum(m[0][j] for j in range(mg + 1))
    p_away_scores = 1.0 - sum(m[i][0] for i in range(mg + 1))
    p_home_15 = sum(m[i][j] for i in range(2, mg + 1) for j in range(mg + 1))
    p_away_15 = sum(m[i][j] for i in range(mg + 1) for j in range(2, mg + 1))

    # distribuição do total de gols (0, 1, 2, ..., 6+)
    totals_dist = {}
    for t in range(0, 7):
        s = 0.0
        for i in range(mg + 1):
            for j in range(mg + 1):
                hit = (i + j == t) if t < 6 else (i + j >= 6)
                if hit:
                    s += m[i][j]
        totals_dist[str(t)] = round(s, 4)

    scores = sorted(
        ((i, j, m[i][j]) for i in range(6) for j in range(6)),
        key=lambda t: -t[2],
    )[:3]

    sample_conf = min((st_h.n_eff + st_a.n_eff) / 24.0, 1.0)
    confidence = round(0.35 + 0.65 * sample_conf, 2)

    # total por time: os dois lados (a v2 só expunha o "marca 0.5+", sem o
    # "menos de", e por isso não conseguia nem calcular vantagem nesses
    # mercados — justamente os que ela mais usava)
    p_home_under_05 = sum(m[0][j] for j in range(mg + 1))
    p_away_under_05 = sum(m[i][0] for i in range(mg + 1))
    p_home_under_15 = 1.0 - p_home_15
    p_away_under_15 = 1.0 - p_away_15

    markets = {
        "over_0.5": p_over(0.5), "over_1.5": p_over(1.5),
        "over_2.5": p_over(2.5), "over_3.5": p_over(3.5),
        "under_0.5": 1 - p_over(0.5), "under_1.5": 1 - p_over(1.5),
        "under_2.5": 1 - p_over(2.5), "under_3.5": 1 - p_over(3.5),
        "btts_yes": p_btts, "btts_no": 1 - p_btts,
        "ht_0.5": p_home_scores, "at_0.5": p_away_scores,
        "ht_1.5": p_home_15, "at_1.5": p_away_15,
        "under_ht_0.5": p_home_under_05, "under_at_0.5": p_away_under_05,
        "under_ht_1.5": p_home_under_15, "under_at_1.5": p_away_under_15,
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
        "totals_dist": totals_dist,
        "joint_model": lam_override is not None,
        "trends_home": _goal_trends(home),
        "trends_away": _goal_trends(away),
    }


def _goal_trends(ts: TeamSample) -> dict:
    """Tendências de gols do time nos últimos 10 jogos (não ponderado)."""
    games = sorted(ts._normalize(), key=lambda g: g[0])[:10]
    if not games:
        return {}
    n = len(games)
    tot = [g[2] + g[3] for g in games]
    return {
        "n": n,
        "avg_for": round(sum(g[2] for g in games) / n, 2),
        "avg_against": round(sum(g[3] for g in games) / n, 2),
        "avg_total": round(sum(tot) / n, 2),
        "over25_rate": round(sum(1 for t in tot if t >= 3) / n, 2),
        "over15_rate": round(sum(1 for t in tot if t >= 2) / n, 2),
        "btts_rate": round(sum(1 for g in games if g[2] > 0 and g[3] > 0) / n, 2),
        "failed_to_score": sum(1 for g in games if g[2] == 0),
        "clean_sheets": sum(1 for g in games if g[3] == 0),
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
    "ht_0.5": "Mandante marca (0.5+)",
    "at_0.5": "Visitante marca (0.5+)",
    "ht_1.5": "Mandante marca 1.5+",
    "at_1.5": "Visitante marca 1.5+",
    "under_ht_0.5": "Mandante NÃO marca",
    "under_at_0.5": "Visitante NÃO marca",
    "under_ht_1.5": "Mandante até 1 gol",
    "under_at_1.5": "Visitante até 1 gol",
    "dc_1x": "Dupla chance 1X",
    "dc_x2": "Dupla chance X2",
    "dc_12": "Dupla chance 12",
}

MARKET_FAMILY = {
    "ht_0.5": "gols", "at_0.5": "gols", "ht_1.5": "gols", "at_1.5": "gols",
    "under_ht_0.5": "gols", "under_at_0.5": "gols",
    "under_ht_1.5": "gols", "under_at_1.5": "gols",
    "over_1.5": "gols", "over_2.5": "gols", "over_3.5": "gols",
    "under_1.5": "gols", "under_2.5": "gols", "under_3.5": "gols",
    "btts_yes": "btts", "btts_no": "btts",
    "home": "resultado", "draw": "resultado", "away": "resultado",
    "dc_1x": "dupla", "dc_x2": "dupla", "dc_12": "dupla",
}


MARKET_GROUPS = [
    ("home", "draw", "away"),
    ("over_0.5", "under_0.5"),
    ("over_1.5", "under_1.5"),
    ("over_2.5", "under_2.5"),
    ("over_3.5", "under_3.5"),
    ("btts_yes", "btts_no"),
    ("dc_1x", "dc_x2", "dc_12"),
    ("ht_0.5", "under_ht_0.5"),
    ("at_0.5", "under_at_0.5"),
    ("ht_1.5", "under_ht_1.5"),
    ("at_1.5", "under_at_1.5"),
]


def de_vig_markets(odds: dict, method: str = "power") -> dict:
    """Probabilidade implícita nas odds com a margem da casa removida.

    Usa o mesmo método de devig configurado no app (proportional/power/shin)
    para que a mistura e a checagem de vantagem falem a mesma língua.
    """
    from . import market as mk
    out = {}
    for group in MARKET_GROUPS:
        if not all(k in odds and odds[k] and odds[k] > 1.0 for k in group):
            continue
        qs = mk.devig([odds[k] for k in group], method)
        if not qs:
            continue
        for k, q in zip(group, qs, strict=False):
            out[k] = q
    return out


def blend_markets(model_markets: dict, odds: dict, model_weight: float = 0.25,
                  method: str = "power") -> dict:
    """Combina probabilidade do modelo (já calibrada) com o consenso do mercado."""
    w = max(0.0, min(model_weight, 1.0))
    if w >= 1.0:
        return dict(model_markets)
    market_probs = de_vig_markets(odds, method)
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


# ------------------------------------------------------------------ seleção v3
# A v2.1 escolhia "o mercado mais provável" — dinâmica que acerta muito e
# perde dinheiro: 83% de acerto a @1,20 devolve −0,4% por aposta *antes* da
# margem. A v3 escolhe por **valor com preço-alvo**: probabilidade na faixa
# saudável, odd acima do piso, vantagem medida sobre o mercado devigado e
# stake dimensionada por Kelly com desconto de incerteza, ordenando por
# **crescimento esperado de capital**.

GOAL_MARKETS = ["over_1.5", "over_2.5", "under_2.5", "btts_yes", "btts_no",
                "over_3.5", "under_3.5"]
GOAL_PICK_MARKETS = ["over_1.5", "ht_0.5", "at_0.5", "over_2.5", "btts_yes",
                     "under_2.5", "ht_1.5", "at_1.5",
                     "btts_no", "over_3.5", "under_3.5", "under_1.5",
                     "under_ht_0.5", "under_at_0.5",
                     "under_ht_1.5", "under_at_1.5"]

# apenas gols/BTTS entram em candidatos e múltiplas
CANDIDATE_MARKETS = list(GOAL_PICK_MARKETS)

# faixas de operação (calibradas no backtest — ver backtest_report.md)
POLICY = {
    "singles": {
        "min_ev": 0.025, "min_edge": 0.015,
        "min_prob": 0.50, "max_prob": 0.85,
        "min_odd": 1.45, "max_odd": 3.20,
    },
    "legs": {
        "min_ev": 0.02, "min_edge": 0.01,
        "min_prob": 0.62, "max_prob": 0.88,
        "min_odd": 1.32, "max_odd": 2.00,
    },
    # 1X2 é mercado eficiente: mesmos limites dos singles, declarados aqui para
    # não cair em fallback silencioso. Só entra quando o usuário liga include_1x2.
    "resultado": {
        "min_ev": 0.025, "min_edge": 0.015,
        "min_prob": 0.45, "max_prob": 0.80,
        "min_odd": 1.45, "max_odd": 3.20,
    },
}

MAX_LEGS = 4
PICK_MIN_PROB = POLICY["singles"]["min_prob"]   # compatibilidade de leitura
PICK_MAX_PROB = POLICY["singles"]["max_prob"]


def _market_index(analysis: dict, odds: dict | None, market: str,
                  devig_method: str) -> float | None:
    """Probabilidade devigada do mercado para o MESMO desfecho (ou None)."""
    from . import market as mk
    if not odds:
        return None
    group = next((g for g in MARKET_GROUPS if market in g), (market,))
    pair = [k for k in group if odds.get(k) and odds[k] > 1.0]
    if len(pair) < 2:
        return None
    qs = mk.devig([odds[k] for k in pair], devig_method)
    for k, q in zip(pair, qs, strict=False):
        if k == market:
            return q
    return None


def market_candidates(analysis: dict, odds: dict | None, *, mode: str = "singles",
                      n_eff: float | None = None,
                      devig_method: str = "power",
                      kelly_mult: float = 0.25, cap: float = 0.03,
                      uncertainty: bool = True,
                      markets: list[str] | tuple[str, ...] | None = None) -> list[dict]:
    """Todos os mercados com valor, em ordem de crescimento esperado.

    Cada item traz a ficha completa: probabilidade do modelo, probabilidade
    devigada do mercado, vantagem, EV, odd mínima aceitável, stake e
    crescimento esperado — além dos motivos de recusa quando não elegível.

    ``markets`` restringe o cardápio (ex.: ``("home", "draw", "away")`` para o
    1X2, que fica fora de ``CANDIDATE_MARKETS`` de propósito: é o mercado mais
    eficiente da casa e raramente paga a margem).
    """
    from . import market as mk
    policy = POLICY.get(mode, POLICY["singles"])
    probs = (analysis or {}).get("markets") or {}
    odds = odds or {}
    out = []
    for market_key in (markets or CANDIDATE_MARKETS):
        p = probs.get(market_key)
        if p is None or p <= 0:
            continue
        odd = odds.get(market_key) or mk.breakeven_odd(p)
        mkt_p = _market_index(analysis, odds, market_key, devig_method)
        card = mk.evaluate(
            p, odd, mkt_p, n_eff=n_eff, kelly_mult=kelly_mult, cap=cap,
            uncertainty=uncertainty, **policy,
        )
        # vantagem do modelo "puro" (pós-calibração, antes da mistura): mostra
        # quanto da divergência sobrevive à diluição com o mercado
        edge_model = None
        model_probs = analysis.get("model_markets") or {}
        if mkt_p is not None and market_key in model_probs:
            edge_model = round(model_probs[market_key] - mkt_p, 4)
        card.update({
            "market": market_key,
            "label": MARKET_LABELS.get(market_key, market_key),
            "family": MARKET_FAMILY.get(market_key, "outros"),
            "odd_is_fair": market_key not in odds,
            "edge_model": edge_model,
        })
        out.append(card)
    out.sort(key=lambda c: (-c["growth"], -c["ev"]))
    return out


def pick_value(analysis: dict, odds: dict | None, *, mode: str = "singles",
               n_eff: float | None = None, **kw) -> dict | None:
    """Melhor entrada elegível (ou ``None`` — a resposta mais comum e honesta)."""
    for card in market_candidates(analysis, odds, mode=mode, n_eff=n_eff, **kw):
        if card["take"]:
            return card
    return None


def watchlist(analysis: dict, odds: dict | None, *, mode: str = "singles",
              n_eff: float | None = None, limit: int = 3, **kw) -> list[dict]:
    """Quase-entradas do jogo: os melhores preços que NÃO passam o filtro.

    Serve para disciplina: mostra o que ficou de fora e por quê (preço curto,
    EV insuficiente), para o usuário não "forçar" a entrada.
    """
    cards = market_candidates(analysis, odds, mode=mode, n_eff=n_eff, **kw)
    return [c for c in cards if not c["take"]][:limit]


def pick_best_market(analysis: dict, odds: dict | None, mode: str = "prob"):
    """Compat: a v2.1 escolhia o mais provável; mantida só para comparação
    histórica no backtest. Produção usa ``pick_value``."""
    probs = (analysis or {}).get("markets") or {}
    odds = odds or {}
    candidates = []
    for pref_i, market_key in enumerate(GOAL_PICK_MARKETS):
        p = probs.get(market_key, 0)
        if not (PICK_MIN_PROB <= p <= PICK_MAX_PROB):
            continue
        odd = odds.get(market_key)
        ev = p * odd - 1 if odd else None
        show_odd = odd or 1 / p
        score = (ev if ev is not None else -9.0) if mode == "ev" else p
        candidates.append((score, -pref_i, market_key, p, show_odd, ev))
    if not candidates:
        return None
    score, _pi, market_key, p, odd, ev = sorted(candidates, key=lambda t: (-t[0], t[1]))[0]
    return {
        "market": market_key,
        "label": MARKET_LABELS[market_key],
        "prob": round(p, 4),
        "odd": round(odd, 2),
        "odd_is_fair": ev is None,
        "ev": round(ev, 4) if ev is not None else None,
        "score": round(score, 4),
    }


def flat_stake(bankroll: float, cap_pct: float = 3.0, fraction: float = 0.5) -> dict:
    """Stake fixa — mantida para o modo legado; a v3 usa Kelly com incerteza."""
    pct = round(cap_pct * fraction, 2)
    return {"stake": round(bankroll * pct / 100, 2), "kelly_full": None,
            "pct": pct, "sigma": None}


def kelly_stake(prob: float, odd: float, bankroll: float,
                fraction: float = 0.25, cap_pct: float = 0.03,
                n_eff: float | None = None, uncertainty: bool = True) -> dict:
    """Kelly fracionado com teto e desconto de incerteza (via app.market)."""
    from . import market as mk
    st = mk.stake_fraction(prob, odd, kelly_mult=fraction, cap=cap_pct,
                           n_eff=n_eff, uncertainty=uncertainty)
    return {
        "stake": round(bankroll * st["kelly_frac"], 2),
        "kelly_full": st["kelly_full"],
        "pct": round(st["kelly_frac"] * 100, 3),
        "sigma": None,
        "p_used": st["p_used"],
    }


def build_multiple(analyzed: list, settings) -> dict | None:
    """Múltipla do dia (v3): pernas elegíveis por valor, escada 1..N, stake Kelly.

    Compatível com a assinatura antiga; a matemática vive em ``app/parlay.py``.
    """
    from . import parlay
    bankroll = getattr(settings, "bankroll", 1000.0)
    faction = getattr(settings, "kelly_fraction", 0.25)
    cap = getattr(settings, "stake_cap_pct", 3.0) / 100.0
    devig_method = getattr(settings, "devig_method", "power")
    cands = []
    for r in analyzed:
        n_eff = r["analysis"].get("n_eff") or r["analysis"].get("sample_home")
        for card in market_candidates(r["analysis"], r.get("odds"), mode="legs",
                                      n_eff=n_eff, devig_method=devig_method,
                                      kelly_mult=faction, cap=cap):
            if not card["take"]:
                continue
            cands.append({
                "fixture_id": r["id"],
                "match": f'{r["home"]["name"]} x {r["away"]["name"]}',
                "league": r["league"],
                "kickoff_utc": r["kickoff_utc"],
                "market": card["market"],
                "label": card["label"],
                "prob": card["prob"],
                "odd": card["odd"],
                "market_prob": card["market_prob"],
                "edge": card["edge"],
                "ev": card["ev"],
                "growth": card["growth"],
                "family": card["family"],
                "n_eff": n_eff,
            })
    # no máximo uma perna por jogo (evita correlação trivial)
    best_per_fixture: dict = {}
    for c in cands:
        cur = best_per_fixture.get(c["fixture_id"])
        if cur is None or c["growth"] > cur["growth"]:
            best_per_fixture[c["fixture_id"]] = c
    pool = list(best_per_fixture.values())
    result = parlay.build(pool, max_legs=MAX_LEGS, kelly_mult=faction, cap=cap,
                          daily_cap_pct=settings_daily_cap(settings))
    if not result.get("available"):
        return None
    slip = result["slip"]
    for leg in result["legs"]:
        leg["anchor"] = leg["odd"] < 1.45
    return {
        "legs": result["legs"],
        "combined_odd": slip["combined_odd"],
        "combined_prob": slip["prob_independent"],
        "combined_prob_correlated": slip["prob_joint"],
        "ev": slip["ev"],
        "rho_avg": slip["rho_avg"],
        "margin_gross": slip["margin_gross"],
        "breakeven_odd": slip["breakeven_odd"],
        "ladder": result["ladder"],
        "stake": {
            "stake": round(bankroll * slip["stake_pct"] / 100.0, 2),
            "pct": slip["stake_pct"],
        },
        "risk_note": result["risk_note"],
    }


def settings_daily_cap(settings) -> float:
    return float(getattr(settings, "daily_cap_pct", 6.0))
