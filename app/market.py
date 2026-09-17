"""Matemática de mercado: probabilidade justa, valor esperado, odd mínima e critério de Kelly.

Esta é a camada que decide **se** uma aposta existe — e não "qual é o palpite".
Todo o resto do app (modelo de gols, xG, dossiê do time) só existe para
alimentar as probabilidades que entram aqui.

Conceitos implementados (padrão de operação profissional):

1. **Remoção de margem (devig)**. A odd não é probabilidade: é probabilidade
   com a margem da casa embutida. Removê-la corretamente importa porque a
   margem não é distribuída de forma uniforme — ela pesa mais no azarão.
   Três métodos disponíveis:
     - ``proportional``: q_i ∝ 1/o_i. Simples, mas historicamente enviesado
       (superestima o azarão, subestima o favorito).
     - ``power``: q_i = (1/o_i)^k, com k resolvido para Σq_i = 1. Corrige o
       viés favorito-azarão e é o que o backtest deste repositório prefere.
     - ``shin``: modelo de Shin (1993), que separa margem de "insider trading"
       implícito; teoricamente mais correto em mercados de 2 resultados.
   A escolha de método é medida no backtest (Brier e ROI), não assumida.

2. **Valor**. ``ev = p·o − 1`` só é confiável se ``p`` estiver calibrada.
   Por isso expomos também ``edge = p − q`` (vantagem em pontos de
   probabilidade sobre o mercado) e a **odd mínima aceitável**
   (``(1+min_ev)/p``): o preço abaixo do qual não se aposta, mesmo gostando
   do palpite. Profissional opera com preço-alvo, não com opinião.

3. **Kelly com incerteza**. A probabilidade ``p`` é uma estimativa com erro.
   Em vez de desconto ad-hoc, usamos o **quantil inferior de uma posterior
   Beta** (pseudo-contagens de ``n_eff`` observações): amostra pequena →
   quantil mais baixo → stake menor. Sem scipy: a função beta incompleta
   regularizada é implementada aqui por fração continuada.

4. **Critério de crescimento (Kelly log-optimal)**. O objetivo real de quem
   aposta é maximizar o crescimento do capital. Ordenar candidatos por
   ``EV`` puro superestima azarões; ordenar por probabilidade pura superestima
   microfavoritos. Ordenamos por **log-crescimento esperado**
   ``G = p·ln(1+f(o−1)) + (1−p)·ln(1−f)`` com a stake fracionada de Kelly —
   que é a única função-objetivo que equilibra odds e probabilidade de forma
   matematicamente consistente.
"""
from __future__ import annotations

import math

DEVIG_METHODS = ("proportional", "power", "shin")
DEFAULT_DEVIG = "power"

# ------------------------------------------------------------------ mercado


def implied_probs(odds: list[float]) -> list[float]:
    """Probabilidade implícita bruta (com margem) de uma lista de odds."""
    return [1.0 / o for o in odds]


def overround(odds: list[float]) -> float:
    """Margem total da casa no mercado (soma das implícitas − 1)."""
    return sum(implied_probs(odds)) - 1.0


def devig_proportional(odds: list[float]) -> list[float]:
    inv = implied_probs(odds)
    s = sum(inv)
    return [x / s for x in inv] if s > 0 else inv


def devig_power(odds: list[float], tol: float = 1e-10, iters: int = 80) -> list[float]:
    """Devig por potência: q_i = (1/o_i)^k, k tal que Σ q_i = 1.

    k > 1 reduz mais as probabilidades maiores (azarões) e menos as menores
    (favoritos) — o padrão de correção do viés favorito-azarão.
    """
    inv = implied_probs(odds)
    if not inv or min(inv) <= 0:
        return inv
    lo, hi = 1e-6, 40.0

    def total(k: float) -> float:
        return sum(x ** k for x in inv)

    # k > 1 quando o overround é positivo (caso normal)
    if total(1.0) <= 1.0:
        return devig_proportional(odds)
    for _ in range(iters):
        mid = (lo + hi) / 2
        if total(mid) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    return [x ** k for x in inv]


def devig_shin(odds: list[float], tol: float = 1e-11, iters: int = 100) -> list[float]:
    """Devig pelo modelo de Shin (1993).

    z estima a fração de "informação privilegiada" no mercado; com z = 0 o
    método degenera em proporcional. Resolve
        q_i = (sqrt(z² + 4(1−z)·π_i²/K) − z) / (2(1−z)),  K = Σπ_i
    com Σq_i = 1 por bisseção em z ∈ [0, 1).
    """
    pi = implied_probs(odds)
    K = sum(pi)
    target = 1.0
    if target >= K or not pi or min(pi) <= 0:
        return devig_proportional(odds)

    def probs(z: float) -> list[float]:
        if z <= 0:
            return [p / K for p in pi]
        denom = 2.0 * (1.0 - z)
        return [(math.sqrt(z * z + 4.0 * (1.0 - z) * (p * p) / K) - z) / denom for p in pi]

    lo, hi = 0.0, 0.999999
    if sum(probs(hi)) > target:
        return probs(hi)
    for _ in range(iters):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return probs((lo + hi) / 2)


def devig(odds: list[float], method: str = DEFAULT_DEVIG) -> list[float]:
    """Remove a margem da casa. Sempre devolve probabilidades somando 1."""
    if not odds or any(o is None or o <= 1.0 for o in odds):
        return []
    if method == "proportional":
        out = devig_proportional(odds)
    elif method == "power":
        out = devig_power(odds)
    elif method == "shin":
        out = devig_shin(odds)
    else:
        raise ValueError(f"método de devig desconhecido: {method}")
    s = sum(out)
    return [x / s for x in out] if s > 0 else out


# ------------------------------------------------------------------ valor


def breakeven_odd(prob: float) -> float:
    """Odd em que a aposta empata (EV = 0) para a probabilidade dada."""
    return 1.0 / prob if prob > 0 else float("inf")


def required_odd(prob: float, min_ev: float = 0.03) -> float:
    """Odd mínima aceitável: preço-alvo que entrega ``min_ev`` de valor."""
    return (1.0 + min_ev) / prob if prob > 0 else float("inf")


def expected_value(prob: float, odd: float) -> float:
    """EV por unidade apostada (0.03 = +3%)."""
    return prob * odd - 1.0


def edge(prob: float, market_prob: float) -> float:
    """Vantagem em pontos de probabilidade sobre o mercado (devigado)."""
    return prob - market_prob


def capped_ev(ev: float, max_ev: float = 0.25) -> float:
    """EV acima de ``max_ev`` quase sempre é erro de dado (time reserva,
    desfalque em massa, jogo sem importância). Tratamos como suspeito."""
    return min(ev, max_ev)


# ------------------------------------------------------------------ beta / kelly


def _betainc(a: float, b: float, x: float) -> float:
    """Beta incompleta regularizada I_x(a,b) (fração continuada de Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - lbeta) / a
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - _betainc(b, a, 1.0 - x)
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < 1e-30:
            c = 1e-30
        f *= c * d
        if abs(1.0 - c * d) < 1e-12:
            break
    return front * (f - 1.0)


def beta_ppf(prob: float, n_eff: float, quantile: float = 0.30) -> float:
    """Quantil inferior da posterior Beta(p·n+1, (1−p)·n+1).

    É a probabilidade "com desconto de incerteza": com n_eff pequeno o quantil
    fica bem abaixo da estimativa pontual; com n_eff grande converge para p.
    """
    n_eff = max(float(n_eff), 0.0)
    a = prob * n_eff + 1.0
    b = (1.0 - prob) * n_eff + 1.0
    lo, hi = 0.0, 1.0
    # quantil 0.5 exato seria o modo/mediana; bisseção direta resolve tudo
    for _ in range(80):
        mid = (lo + hi) / 2
        if _betainc(a, b, mid) < quantile:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def kelly_fraction(prob: float, odd: float) -> float:
    """Fração de Kelly cheia (pode ser negativa = não apostar)."""
    b = odd - 1.0
    if b <= 0:
        return -1.0
    return (prob * odd - 1.0) / b


def stake_fraction(prob: float, odd: float, *, kelly_mult: float = 0.25,
                   cap: float = 0.03, n_eff: float | None = None,
                   beta_quantile: float = 0.30, uncertainty: bool = True) -> dict:
    """Fração da banca a apostar, com teto e desconto de incerteza.

    Devolve o detalhamento (probabilidade usada, Kelly cheia, fração final)
    para que a decisão seja auditável na interface.
    """
    p_raw = prob
    p_used = beta_ppf(prob, n_eff, beta_quantile) if uncertainty and n_eff else prob
    k = kelly_fraction(p_used, odd)
    if k <= 0:
        return {"pct": 0.0, "kelly_full": 0.0, "kelly_frac": 0.0,
                "p_used": round(p_used, 4), "p_raw": round(p_raw, 4)}
    frac = min(k * kelly_mult, cap)
    return {
        "pct": round(100 * frac, 3),
        "kelly_full": round(max(k, 0.0), 4),
        "kelly_frac": round(frac, 5),
        "p_used": round(p_used, 4),
        "p_raw": round(p_raw, 4),
    }


def growth_score(prob: float, odd: float, *, kelly_mult: float = 0.25,
                 cap: float = 0.03, n_eff: float | None = None,
                 beta_quantile: float = 0.30, uncertainty: bool = True) -> float:
    """Log-crescimento esperado por aposta, na fração de stake sugerida.

    ``G = p·ln(1+f(o−1)) + (1−p)·ln(1−f)``. É a função-objetivo do apostador
    (maximiza crescimento de capital no longo prazo) e a métrica de ordenação
    do app: ela penaliza tanto a probabilidade baixa quanto a odd baixa.
    Negativo = a aposta destrói capital esperado.
    """
    st = stake_fraction(prob, odd, kelly_mult=kelly_mult, cap=cap,
                        n_eff=n_eff, beta_quantile=beta_quantile,
                        uncertainty=uncertainty)
    f = st["kelly_frac"]
    if f <= 0:
        return -1.0 if expected_value(prob, odd) <= 0 else 0.0
    return prob * math.log(1.0 + f * (odd - 1.0)) + (1.0 - prob) * math.log(1.0 - f)


# ------------------------------------------------------------------ apostas


def evaluate(prob: float, odd: float, market_prob: float | None = None, *,
             min_ev: float = 0.025, min_edge: float = 0.015,
             min_prob: float = 0.50, max_prob: float = 0.85,
             min_odd: float = 1.40, max_odd: float = 3.00,
             n_eff: float | None = None, kelly_mult: float = 0.25,
             cap: float = 0.03, beta_quantile: float = 0.30,
             uncertainty: bool = True) -> dict:
    """Ficha completa de decisão de um mercado: apostar ou não, e por quê.

    Devolve sempre um dicionário com o veredito (``take``), os motivos de
    recusa (``reasons``) e os números que sustentam a decisão. A recusa é
    informação de primeira classe: "não apostar" é a decisão mais frequente
    de quem lucra.
    """
    ev = expected_value(prob, odd)
    reasons: list[str] = []
    if market_prob is not None and edge(prob, market_prob) < min_edge:
        reasons.append(f"vantagem sobre o mercado abaixo do mínimo "
                       f"({edge(prob, market_prob):+.1%} < {min_edge:.1%})")
    # Sem o outro lado do mercado não dá para separar margem de valor: exige
    # 2 pp extra de EV (a margem típica de um par de gols é ~2–4%).
    min_ev_eff = min_ev if market_prob is not None else min_ev + 0.02
    if ev < min_ev_eff:
        extra = " (exigido +2pp: sem o outro lado do mercado)" if market_prob is None else ""
        reasons.append(f"EV {ev:+.1%} abaixo do mínimo {min_ev_eff:.1%}{extra}")
    if ev > 0.25:
        reasons.append(f"EV {ev:+.1%} suspeito (acima de 25%): confira "
                       f"escalação/provável erro de dado")
    if prob < min_prob:
        reasons.append(f"probabilidade {prob:.1%} abaixo da faixa "
                       f"({min_prob:.0%}–{max_prob:.0%})")
    if prob > max_prob:
        reasons.append(f"probabilidade {prob:.1%} acima da faixa: a odd não "
                       f"paga o risco de fila")
    if odd < min_odd:
        reasons.append(f"odd {odd:.2f} abaixo do piso {min_odd:.2f}")
    if odd > max_odd:
        reasons.append(f"odd {odd:.2f} acima do teto {max_odd:.2f}")

    st = stake_fraction(prob, odd, kelly_mult=kelly_mult, cap=cap,
                        n_eff=n_eff, beta_quantile=beta_quantile,
                        uncertainty=uncertainty)
    g = growth_score(prob, odd, kelly_mult=kelly_mult, cap=cap,
                     n_eff=n_eff, beta_quantile=beta_quantile,
                     uncertainty=uncertainty)
    if st["pct"] <= 0:
        reasons.append("Kelly zerado: sem stake positiva para esta crença")
    return {
        "take": not reasons,
        "reasons": reasons,
        "prob": round(prob, 4),
        "market_prob": None if market_prob is None else round(market_prob, 4),
        "edge": None if market_prob is None else round(edge(prob, market_prob), 4),
        "odd": round(odd, 2),
        "ev": round(ev, 4),
        "breakeven_odd": round(breakeven_odd(prob), 2),
        "required_odd": round(required_odd(prob, min_ev), 2),
        "min_ev_used": round(min_ev_eff, 4),
        "growth": round(g, 6),
        "stake_pct": st["pct"],
        "kelly_full": st["kelly_full"],
        "p_used": st["p_used"],
    }


def effective_margin(odds: list[float], fair_probs: list[float]) -> float:
    """Margem efetiva de uma combinada: quanto do valor justo a casa retém.

    Para uma múltipla de n pernas a margem não se soma, **multiplica**:
    ``∏(1/o_i) / ∏q_i − 1``. É a razão matemática pela qual múltipla é o
    produto mais rentável da casa e o mais perigoso para o apostador.
    """
    if not odds or len(odds) != len(fair_probs):
        return 0.0
    book = 1.0
    fair = 1.0
    for o, q in zip(odds, fair_probs, strict=False):
        book *= 1.0 / o
        fair *= q
    if fair <= 0:
        return 0.0
    return book / fair - 1.0
