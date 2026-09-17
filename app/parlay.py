"""Combinadas (múltiplas) com matemática honesta.

Múltipla não é "vários singles no mesmo bilhete": a margem da casa
**multiplica** (``∏(1/o_i)/∏q_i − 1``), e a probabilidade conjunta cai rápido.
Uma múltipla de 4 pernas a 80% cada acerta 41% das vezes, não 80%.

Este módulo:

* monta a escada de 1 a 5 pernas com probabilidade conjunta, odd, EV, margem
  embutida, stake de Kelly e **crescimento esperado** — a métrica que diz
  quantas pernas valem a pena, em vez de "quanto maior a odd, melhor";
* aplica **correlação** entre pernas da mesma liga/rodada (o clima de gols de
  uma rodada é comum: defesas cansadas, arbitragem, clima). A fórmula usa o
  modelo normal latente: ``P(A∩B) ≈ p_A·p_B + ρ·φ(z_A)·φ(z_B)``, validada por
  simulação em ``tests/``;
* respeita o **teto de exposição diária**: nenhuma combinação de bilhetes do
  dia pode passar de X% da banca;
* recusa o bilhete quando o crescimento esperado é negativo — o que acontece
  na maioria dos dias, e tudo bem.
"""
from __future__ import annotations

import math

from . import market

# correlação padrão entre pernas (medida no backtest; ver backtest_report.md)
RHO_SAME_LEAGUE_SAME_FAMILY = 0.06
RHO_SAME_LEAGUE = 0.03
RHO_CROSS = 0.0
DEFAULT_MAX_LEGS = 4
DEFAULT_DAILY_CAP_PCT = 6.0
DEFAULT_MAX_COMBINED_ODD = 8.0   # odd acima disso é bilhete de loteria, não aposta


# ------------------------------------------------------------------ normal


def _norm_cdf(x: float) -> float:
    """CDF normal padrão (Abramowitz & Stegun 26.2.17, erro < 1e-7)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inversa da normal padrão (Beasley-Springer-Moro)."""
    if not 0.0 < p < 1.0:
        return float("-inf") if p <= 0 else float("inf")
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def joint_prob(probs: list[float], rho: float = 0.0) -> float:
    """Probabilidade conjunta com correlação (modelo normal latente).

    Expansão de primeira ordem da probabilidade da normal multivariada em
    torno da independência::

        P(∩A_i) ≈ (∏ p_i) · [1 + Σ_{i<j} ρ_ij · φ(z_i)·φ(z_j) / (p_i·p_j)]

    com ``z_i = Φ⁻¹(p_i)``. Para duas pernas a fórmula é exata no modelo
    latente (validada por simulação em ``tests/test_parlay.py``); para mais,
    o erro é pequeno enquanto ρ for pequeno.

    Observação de risco: com ρ > 0 a probabilidade conjunta **sobe** (clima de
    gols compartilhado ajuda quem aposta as pernas juntas). Por isso as
    decisões do app usam a probabilidade de independência como piso
    conservador e reportam a corrigida apenas como cenário.
    """
    if len(probs) < 2:
        return probs[0] if probs else 0.0
    base = math.prod(probs)
    if rho == 0.0:
        return base
    zs = [norm_ppf(p) for p in probs]
    pdfs = [math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) for z in zs]
    extra = 0.0
    for i in range(len(probs)):
        for j in range(i + 1, len(probs)):
            denom = probs[i] * probs[j]
            if denom > 0:
                extra += rho * pdfs[i] * pdfs[j] / denom
    joint = base * (1.0 + extra)
    return min(max(joint, 1e-9), min(probs))


def correlation_for(leg_a: dict, leg_b: dict) -> float:
    """Correlação estimada entre duas pernas."""
    if not leg_a or not leg_b:
        return 0.0
    same_league = leg_a.get("league") and leg_a.get("league") == leg_b.get("league")
    same_family = leg_a.get("family") and leg_a.get("family") == leg_b.get("family")
    if same_league and same_family:
        return RHO_SAME_LEAGUE_SAME_FAMILY
    if same_league:
        return RHO_SAME_LEAGUE
    return RHO_CROSS


def _avg_rho(legs: list[dict]) -> float:
    vals = []
    for i in range(len(legs)):
        for j in range(i + 1, len(legs)):
            vals.append(correlation_for(legs[i], legs[j]))
    return sum(vals) / len(vals) if vals else 0.0


# ------------------------------------------------------------------ bilhete


def slip_math(legs: list[dict], *, kelly_mult: float = 0.25, cap: float = 0.03,
              n_eff: float | None = None, beta_quantile: float = 0.30,
              uncertainty: bool = True, rho: float | None = None) -> dict:
    """Números completos de um bilhete combinado."""
    if not legs:
        return {}
    odds = [leg["odd"] for leg in legs]
    probs = [leg["prob"] for leg in legs]
    combined_odd = math.prod(odds)
    indep = math.prod(probs)
    rho_avg = _avg_rho(legs) if rho is None else rho
    joint = joint_prob(probs, rho_avg)
    margin = market.effective_margin(odds, [1.0 / max(o, 1.01) for o in odds])
    ev_indep = indep * combined_odd - 1.0
    ev_joint = joint * combined_odd - 1.0
    # decisão no cenário conservador (independência); correlação é upside
    st = market.stake_fraction(indep, combined_odd, kelly_mult=kelly_mult,
                               cap=cap, n_eff=n_eff, beta_quantile=beta_quantile,
                               uncertainty=uncertainty)
    growth = market.growth_score(indep, combined_odd, kelly_mult=kelly_mult,
                                 cap=cap, n_eff=n_eff, beta_quantile=beta_quantile,
                                 uncertainty=uncertainty)
    growth_corr = market.growth_score(joint, combined_odd, kelly_mult=kelly_mult,
                                      cap=cap, n_eff=n_eff, beta_quantile=beta_quantile,
                                      uncertainty=uncertainty)
    return {
        "legs": len(legs),
        "combined_odd": round(combined_odd, 2),
        "prob_independent": round(indep, 4),
        "prob_joint": round(joint, 4),
        "rho_avg": round(rho_avg, 4),
        "ev_independent": round(ev_indep, 4),
        "ev": round(ev_joint, 4),
        "margin_gross": round(margin, 4),
        "stake_pct": st["pct"],
        "kelly_full": st["kelly_full"],
        "p_used": st["p_used"],
        "growth": round(growth, 6),
        "growth_correlated": round(growth_corr, 6),
        "breakeven_odd": round(market.breakeven_odd(indep), 2),
        "required_odd": round(market.required_odd(indep, 0.02), 2),
    }


def leg_ticket(analysis: dict, odds: dict | None, market_key: str, *,
               min_prob: float = 0.62, max_prob: float = 0.88,
               min_odd: float = 1.32, max_odd: float = 2.00,
               min_ev: float = 0.02, min_edge: float = 0.01,
               n_eff: float | None = None, devig_method: str = market.DEFAULT_DEVIG,
               family: str | None = None) -> dict | None:
    """Avalia um mercado como perna de combinada."""
    probs = (analysis or {}).get("markets") or {}
    p = probs.get(market_key)
    if p is None or p <= 0:
        return None
    odd = (odds or {}).get(market_key) or market.breakeven_odd(p)
    mkt_p = _market_prob_for(analysis, odds, market_key, devig_method)
    ev = market.expected_value(p, odd)
    if ev < min_ev:
        return None
    if not (min_prob <= p <= max_prob) or not (min_odd <= odd <= max_odd):
        return None
    if mkt_p is not None and market.edge(p, mkt_p) < min_edge:
        return None
    return {
        "market": market_key,
        "prob": round(p, 4),
        "odd": round(odd, 2),
        "market_prob": None if mkt_p is None else round(mkt_p, 4),
        "edge": None if mkt_p is None else round(p - mkt_p, 4),
        "ev": round(ev, 4),
        "growth": round(market.growth_score(p, odd, n_eff=n_eff), 6),
        "family": family or _family(market_key),
        "odd_is_fair": odds is None or market_key not in (odds or {}),
    }


def _market_prob_for(analysis: dict, odds: dict | None, market_key: str,
                     devig_method: str = market.DEFAULT_DEVIG) -> float | None:
    """Probabilidade devigada do mercado para o mesmo desfecho, se houver par."""
    if not odds:
        return None
    group = _group_of(market_key)
    pair = [k for k in group if k in odds and odds[k] and odds[k] > 1.0]
    if len(pair) < 2:
        return None
    qs = market.devig([odds[k] for k in pair], devig_method)
    for k, q in zip(pair, qs, strict=False):
        if k == market_key:
            return q
    return None


def _group_of(market_key: str) -> list[str]:
    from .model import MARKET_GROUPS
    for g in MARKET_GROUPS:
        if market_key in g:
            return list(g)
    return [market_key]


def _family(market_key: str) -> str:
    from .model import MARKET_FAMILY
    return MARKET_FAMILY.get(market_key, "outros")


# ------------------------------------------------------------------ escada


def ladder(cands: list[dict], *, max_legs: int = DEFAULT_MAX_LEGS,
           kelly_mult: float = 0.25, cap: float = 0.03,
           uncertainty: bool = True) -> list[dict]:
    """Escada de 1 a N pernas, escolhendo por crescimento esperado.

    A resposta honesta para "quantas pernas?" não é um dogma: é a perna em que
    o crescimento esperado marginal deixa de compensar o risco adicional.
    """
    pool = sorted(cands, key=lambda c: -c.get("growth", -9))
    out = []
    for k in range(1, max_legs + 1):
        leg = pool[:k]
        if len(leg) < k:
            break
        n_eff = min([c.get("n_eff") or 20 for c in leg])
        m = slip_math(leg, kelly_mult=kelly_mult, cap=cap, n_eff=n_eff,
                      uncertainty=uncertainty)
        m["k"] = k
        m["recommended"] = m["ev"] > 0 and m["growth"] > 0
        out.append(m)
    return out


def build(cands: list[dict], *, max_legs: int = DEFAULT_MAX_LEGS,
          kelly_mult: float = 0.25, cap: float = 0.03,
          daily_cap_pct: float = DEFAULT_DAILY_CAP_PCT,
          uncertainty: bool = True, min_legs: int = 2,
          max_combined_odd: float = DEFAULT_MAX_COMBINED_ODD) -> dict:
    """Bilhete do dia + escada + avisos de correlação e exposição.

    A escolha do número de pernas respeita três limites: crescimento esperado
    positivo, odd combinada dentro do teto (acima de ~8 a combinação deixa de
    ser aposta e vira bilhete de loteria) e stake dentro do teto diário.
    """
    if not cands:
        return {"available": False, "reason": "nenhuma perna elegível hoje",
                "ladder": []}
    pool = sorted(cands, key=lambda c: -c.get("growth", -9))
    best_k = None
    best_growth = 0.0
    stair = ladder(pool, max_legs=max_legs, kelly_mult=kelly_mult, cap=cap,
                   uncertainty=uncertainty)
    for step in stair:
        if step["k"] < min_legs or not step["recommended"]:
            continue
        if step["combined_odd"] > max_combined_odd:
            continue
        if step["growth"] > best_growth:
            best_growth, best_k = step["growth"], step["k"]
    chosen = pool[:best_k] if best_k else []
    slip = slip_math(chosen, kelly_mult=kelly_mult, cap=cap,
                     n_eff=min([c.get("n_eff") or 20 for c in chosen]) if chosen else None,
                     uncertainty=uncertainty) if chosen else {}
    if slip:
        slip["k"] = len(chosen)
    # exposição: stake do bilhete + singles devem respeitar o teto diário
    return {
        "available": bool(chosen),
        "reason": None if chosen else "nenhuma combinação com crescimento esperado positivo",
        "legs": chosen,
        "slip": slip,
        "ladder": stair,
        "daily_cap_pct": daily_cap_pct,
        "max_combined_odd": max_combined_odd,
        "correlation": {
            "rho_avg": round(_avg_rho(chosen), 4) if chosen else 0.0,
            "note": ("pernas da mesma liga/rodada compartilham clima de gols; "
                     "a probabilidade conjunta já está corrigida por ρ"),
        },
        "risk_note": (
            "Múltipla multiplica a margem da casa e reduz a chance de acerto: "
            "só entre com crescimento esperado positivo e stake reduzida."
        ),
    }


def daily_exposure(slips: list[dict], singles_pct: float = 0.0,
                   daily_cap_pct: float = DEFAULT_DAILY_CAP_PCT) -> dict:
    """Controla a exposição total do dia (singles + múltiplas)."""
    total = singles_pct + sum(s.get("stake_pct", 0.0) for s in slips)
    return {
        "total_pct": round(total, 3),
        "cap_pct": daily_cap_pct,
        "within_cap": total <= daily_cap_pct,
        "headroom_pct": round(max(daily_cap_pct - total, 0.0), 3),
    }
