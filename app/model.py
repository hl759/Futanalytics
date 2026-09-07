"""Motor estatístico FutAnalytics.

Metodologia:
1. Força de ataque/defesa por time, separada em casa/fora, estimada a partir
   dos últimos jogos com decaimento exponencial no tempo (jogos recentes pesam mais).
2. Gols esperados (lambda) de cada time via modelo multiplicativo:
      lambda_casa = media_liga_casa * ataque_casa * defesa_visitante
      lambda_fora = media_liga_fora * ataque_fora * defesa_casa
   As médias da liga (casa/fora) são estimadas do histórico recente dos times
   da própria liga, com shrinkage para médias globais quando a amostra é pequena.
3. Matriz de placares por Poisson bivariado com correção de Dixon-Coles
   (rho) para placares baixos (0-0, 1-0, 0-1, 1-1), que o Poisson puro distorce.
4. Probabilidades de mercado derivadas da matriz: Over/Under, BTTS, 1X2, dupla chance.
5. Regularização bayesiana: com poucos jogos na amostra, as forças são puxadas
   para a média (shrinkage), evitando extremos por amostra pequena.
6. Quando há odds reais, as probabilidades do modelo são combinadas com o
   consenso do mercado (probabilidade implícita nas odds, com a margem das casas
   removida), por média ponderada com peso configurável.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Priores globais de liga (gols médios casa/fora no futebol profissional; usadas
# como âncora quando não há média específica da liga disponível)
GLOBAL_HOME_AVG = 1.49
GLOBAL_AWAY_AVG = 1.17
DECAY = 0.055          # decaimento exponencial por dia de idade do jogo
SHRINK_GAMES = 6.0     # peso equivalente em jogos puxando para a média
RHO = -0.09            # correção Dixon-Coles para placares baixos
MAX_GOALS = 10
# Quantos jogos "fictícios" ancoram a média da liga na média global
# (shrinkage no nível da liga, distinto do SHRINK_GAMES no nível do time)
LEAGUE_PRIOR_STRENGTH = 60.0


@dataclass
class TeamSample:
    """Amostra de jogos recentes de um time (mais recente primeiro)."""
    name: str
    # cada item: (dias_atras, foi_mandante, gols_pro, gols_contra)
    games: list = field(default_factory=list)

    def strengths(self, home_avg: float = GLOBAL_HOME_AVG,
                  away_avg: float = GLOBAL_AWAY_AVG):
        """Retorna (ataque_casa, defesa_casa, ataque_fora, defesa_fora, n_efetivo, forma)."""
        wh_att = wh_def = wa_att = wa_def = 0.0
        nh = na = 0.0
        form_pts = []
        for days, home, gf, ga in self.games:
            w = math.exp(-DECAY * max(days, 0))
            if home:
                wh_att += w * gf
                wh_def += w * ga
                nh += w
            else:
                wa_att += w * gf
                wa_def += w * ga
                na += w
            if len(form_pts) < 5:
                form_pts.append(3 if gf > ga else (1 if gf == ga else 0))

        # médias ponderadas com shrinkage para as priores de liga
        def shrink(total, n, prior):
            return (total + prior * SHRINK_GAMES) / (n + SHRINK_GAMES)

        gh_att = shrink(wh_att, nh, home_avg)
        gh_def = shrink(wh_def, nh, away_avg)
        ga_att = shrink(wa_att, na, away_avg)
        ga_def = shrink(wa_def, na, home_avg)

        atk_home = gh_att / home_avg
        def_home = gh_def / away_avg
        atk_away = ga_att / away_avg
        def_away = ga_def / home_avg

        n_eff = nh + na
        form = sum(form_pts) / (3 * len(form_pts)) if form_pts else 0.5
        return atk_home, def_home, atk_away, def_away, n_eff, form


def league_priors(games):
    """Médias de gols casa/fora de uma liga, com shrinkage para as globais.

    games: lista de (dias_atras, foi_mandante, gols_pro, gols_contra).
    Gols do mandante somam na média casa; gols do visitante somam na média fora.
    Com poucos jogos na amostra, o valor recua para GLOBAL_HOME_AVG/GLOBAL_AWAY_AVG.
    """
    hg = ag = 0.0
    nh = na = 0.0
    for _days, is_home, gf, _ga in games:
        if is_home:
            hg += gf
            nh += 1
        else:
            ag += gf
            na += 1
    home_avg = (hg + GLOBAL_HOME_AVG * LEAGUE_PRIOR_STRENGTH) / (nh + LEAGUE_PRIOR_STRENGTH)
    away_avg = (ag + GLOBAL_AWAY_AVG * LEAGUE_PRIOR_STRENGTH) / (na + LEAGUE_PRIOR_STRENGTH)
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
    m = [[0.0] * (MAX_GOALS + 1) for _ in range(MAX_GOALS + 1)]
    total = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            p = poisson_pmf(lh, i) * poisson_pmf(la, j) * dixon_coles_tau(i, j, lh, la, RHO)
            p = max(p, 0.0)
            m[i][j] = p
            total += p
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            m[i][j] /= total
    return m


def analyze_match(home: TeamSample, away: TeamSample,
                  home_avg: float | None = None, away_avg: float | None = None):
    if home_avg is None:
        home_avg = GLOBAL_HOME_AVG
    if away_avg is None:
        away_avg = GLOBAL_AWAY_AVG

    ah_atk, ah_def, _, _, n_home, form_home = home.strengths(home_avg, away_avg)
    _, _, aw_atk, aw_def, n_away, form_away = away.strengths(home_avg, away_avg)

    lam_home = home_avg * ah_atk * aw_def
    lam_away = away_avg * aw_atk * ah_def
    lam_home = min(max(lam_home, 0.15), 4.5)
    lam_away = min(max(lam_away, 0.10), 4.0)

    m = score_matrix(lam_home, lam_away)

    p_home = sum(m[i][j] for i in range(MAX_GOALS + 1) for j in range(MAX_GOALS + 1) if i > j)
    p_draw = sum(m[i][i] for i in range(MAX_GOALS + 1))
    p_away = 1 - p_home - p_draw

    def p_over(line: float) -> float:
        thr = int(line) + 1
        return sum(
            m[i][j]
            for i in range(MAX_GOALS + 1)
            for j in range(MAX_GOALS + 1)
            if i + j >= thr
        )

    p_btts = sum(
        m[i][j] for i in range(1, MAX_GOALS + 1) for j in range(1, MAX_GOALS + 1)
    )

    # placares mais prováveis
    scores = sorted(
        ((i, j, m[i][j]) for i in range(6) for j in range(6)),
        key=lambda t: -t[2],
    )[:3]

    # confiança do modelo: amostra efetiva + equilíbrio da forma
    sample_conf = min((n_home + n_away) / 16.0, 1.0)
    confidence = round(0.35 + 0.65 * sample_conf, 2)

    markets = {
        "over_0.5": p_over(0.5),
        "over_1.5": p_over(1.5),
        "over_2.5": p_over(2.5),
        "over_3.5": p_over(3.5),
        "under_0.5": 1 - p_over(0.5),
        "under_1.5": 1 - p_over(1.5),
        "under_2.5": 1 - p_over(2.5),
        "under_3.5": 1 - p_over(3.5),
        "btts_yes": p_btts,
        "btts_no": 1 - p_btts,
        "home": p_home,
        "draw": p_draw,
        "away": p_away,
        "dc_1x": p_home + p_draw,
        "dc_x2": p_draw + p_away,
        "dc_12": p_home + p_away,
    }

    return {
        "lambda_home": round(lam_home, 2),
        "lambda_away": round(lam_away, 2),
        "expected_goals_total": round(lam_home + lam_away, 2),
        "markets": {k: round(v, 4) for k, v in markets.items()},
        "fair_odds": {k: round(1 / v, 2) if v > 0.01 else 99.0 for k, v in markets.items()},
        "top_scores": [
            {"score": f"{i}x{j}", "prob": round(p, 4)} for i, j, p in scores
        ],
        "form_home": round(form_home, 2),
        "form_away": round(form_away, 2),
        "sample_home": round(n_home, 1),
        "sample_away": round(n_away, 1),
        "confidence": confidence,
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

# Grupos de resultados mutuamente exclusivos (para remover margem e misturar
# preservando complementaridade, ex.: over_2.5 + under_2.5 = 1)
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
    """Probabilidade implícita nas odds com a margem da casa removida.

    Usa o método proporcional dentro de cada grupo de resultados (ex.: 1X2),
    normalizando para que o grupo some 1. Só remove margem quando o grupo está
    completo; grupos incompletos são ignorados.
    """
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


def blend_markets(model_markets: dict, odds: dict, model_weight: float = 0.5) -> dict:
    """Combina probabilidade do modelo com o consenso do mercado.

    model_weight = 1 usa só o modelo; 0 usa só o mercado. A mistura é feita por
    grupo de resultados e renormalizada, preservando Over+Under=1 e 1X2=1.
    Mercados sem odds (ou com grupo incompleto) mantêm a probabilidade do modelo.
    """
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


# Mercados de gols primeiro (prioridade do usuário), depois os demais
GOAL_MARKETS = ["over_1.5", "over_2.5", "under_2.5", "btts_yes", "btts_no", "over_3.5", "under_3.5"]
OTHER_MARKETS = ["dc_1x", "dc_x2", "home", "away"]


def pick_best_market(analysis: dict, odds: dict | None):
    """
    Escolhe o melhor mercado do jogo.
    Com odds reais: maior EV positivo (prioridade a mercados de gols).
    Sem odds: mercado de gols com prob entre 0.62 e 0.88 (evita odds micro)
    mais distante do 'coin flip'.
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
                fraction: float = 0.25, cap_pct: float = 0.03) -> dict:
    """Kelly fracionado com teto de risco por aposta."""
    b = odd - 1
    if b <= 0:
        return {"stake": 0.0, "kelly_full": 0.0, "pct": 0.0}
    q = 1 - prob
    k = (b * prob - q) / b
    k = max(k, 0.0)
    frac = min(k * fraction, cap_pct)
    return {
        "stake": round(bankroll * frac, 2),
        "kelly_full": round(k, 4),
        "pct": round(frac * 100, 2),
    }
