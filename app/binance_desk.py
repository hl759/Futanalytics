"""Mesa Binance (SigmaDesk × Binance) — Inteligência Quantitativa e Execução Disciplinada.

Responde à pergunta central do investidor:
    "Qual é o melhor mercado da Binance para operar de forma consistente e
     lucrativa aproveitando a arquitetura preditiva do SigmaDesk?"

A resposta quantitativa (sem ilusão de day trade alavancado):
------------------------------------------------------------------------------
1. PILAR 1 — RENDA ESTRUTURAL (60–70% do capital):
   **Futuros USDⓈ-M / COIN-M em Cash-and-Carry (Funding Rate & Basis Carry)**
   Compra Spot + Venda 1x Perpétuo (ou Trimestral). Exposição direcional ZERO
   (Delta-Neutro). O varejo na Binance opera comprado alavancado e paga Funding
   Rate a cada 8h (ou prêmio de contango no futuro trimestral) para quem carrega
   a ponta vendida. É o mercado mais líquido do mundo, com menor spread (0,01%)
   e edge estrutural positivo sem precisar adivinhar direção.

2. PILAR 2 — MULTIPLICADOR DE ALFA / VOLATILIDADE (30–40% do capital):
   **Venda Coberta via Dual Investment (quando VRP > +3 pts) & Compra Tática de
   Convexidade na Binance Options (quando VRP < 0)**
   Na Binance Options (`eapi`), contas de varejo não podem lançar (vender) opções
   a descoberto — só Market Makers/VIP. Quem tenta operar Binance Options todo
   dia comprando Call/Put perde para o theta e para o VRP.
   Como o investidor inteligente captura o VRP dentro da Binance:
   * Quando VRP está RICO (IV >> RV prevista pelo SigmaDesk): vende volatilidade
     com risco 100% coberto via **Binance Dual Investment** (Buy Low = Short Put
     coberta em USDT; Sell High = Covered Call em BTC/ETH) — MAS só entra se a
     APR paga pela Binance superar a **APR Mínima Justa** calculada pelo nosso
     motor Black-Scholes + Jump-Diffusion sobre a superfície institucional.
   * Quando VRP está NEGATIVO (IV < RV prevista, como em 26/set/2026 com VRP
     de −3,8 pts): **bloqueia** qualquer venda de vol/Dual Investment e habilita
     **Compra de Convexidade (Long Straddle / OTM Call-Put)** na Binance Options,
     com perda máxima estritamente limitada ao prêmio pago.

3. PILAR 3 — TÁTICO DIRECIONAL (Opcional, Vol-Targeted):
   **Swing em Perpétuos USDⓈ-M dimensionado pelo inverso da Volatilidade (Yang-Zhang)**
   Zero alavancagem fixa de cassino: a alavancagem máxima é `σ_alvo / σ_prevista`
   (tipicamente 0,5x a 1,5x), com stop técnico fora do ruído diário (1,5 × σ_dia
   de Yang-Zhang) e tamanho travado pela perda nos 3 cenários de estresse.

Toda a matemática roda em Python puro (stdlib: `math.erf`, `math.log`, `math.exp`),
zero dependências novas, compatível com os 512 MB do Render Free.
"""
from __future__ import annotations

import math

DAYS_PER_YEAR = 365.0

# Estrutura real de custos da Binance (conta regular / VIP 0):
# - Spot: 0,10% (ou 0,075% com desconto BNB)
# - Futuros USDⓈ-M: 0,02% maker / 0,05% taker
# - Ida e volta (montagem + desmontagem) em Cash-and-Carry (Spot + Perp Maker):
BINANCE_FEES = {
    "spot_taker_pct": 0.10,
    "spot_bnb_pct": 0.075,
    "futures_maker_pct": 0.02,
    "futures_taker_pct": 0.05,
    "options_tx_pct": 0.03,
    "carry_roundtrip_standard_pct": 0.28,   # 2×(0.10 spot + 0.04 perp médio)
    "carry_roundtrip_maker_bnb_pct": 0.19,  # 2×(0.075 spot BNB + 0.02 perp maker)
}


# ------------------------------------------------------------------ Black-Scholes & Normal (stdlib)
def norm_cdf(x: float) -> float:
    """Normal acumulada padrão Φ(x) via math.erf (precisão de máquina, sem scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_option(spot: float, strike: float, dte_days: float, iv_pct: float,
              kind: str = "P", r: float = 0.0) -> dict:
    """Precificação Black-Scholes-Merton + Gregas em Python puro.

    `iv_pct` entra em porcentagem anualizada (ex.: 35.4 = 35,4%).
    Devolve preço em USD, % do spot, delta, gamma, vega (por 1 ponto de IV),
    theta diário e probabilidade risk-neutral de exercício.
    """
    if spot <= 0 or strike <= 0 or dte_days <= 0 or iv_pct <= 0:
        return {"price_usd": 0.0, "price_pct": 0.0, "delta": 0.0,
                "gamma": 0.0, "vega": 0.0, "theta_day": 0.0, "prob_itm": 0.0}
    T = max(dte_days / DAYS_PER_YEAR, 1e-6)
    sigma = max(iv_pct / 100.0, 1e-4)
    sqrtT = math.sqrt(T)
    d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc = math.exp(-r * T)

    if kind.upper().startswith("C"):
        price = spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
        delta = norm_cdf(d1)
        prob_itm = norm_cdf(d2)
        theta_yr = (-(spot * norm_pdf(d1) * sigma) / (2.0 * sqrtT)
                    - r * strike * disc * norm_cdf(d2))
    else:
        price = strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1.0
        prob_itm = norm_cdf(-d2)
        theta_yr = (-(spot * norm_pdf(d1) * sigma) / (2.0 * sqrtT)
                    + r * strike * disc * norm_cdf(-d2))

    gamma = norm_pdf(d1) / (spot * sigma * sqrtT)
    # vega por +1 ponto percentual de IV (0.01)
    vega_pt = spot * norm_pdf(d1) * sqrtT * 0.01
    return {
        "price_usd": round(price, 2),
        "price_pct": round((price / spot) * 100.0, 4),
        "apr_equiv_pct": round((price / strike) * (DAYS_PER_YEAR / dte_days) * 100.0, 2)
                         if kind.upper().startswith("P")
                         else round((price / spot) * (DAYS_PER_YEAR / dte_days) * 100.0, 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "vega": round(vega_pt, 2),
        "theta_day": round(theta_yr / DAYS_PER_YEAR, 2),
        "prob_itm": round(prob_itm, 4),
    }


def implied_vol_from_apr(spot: float, strike: float, dte_days: float,
                         apr_pct: float, kind: str = "P") -> float | None:
    """Inverte o Black-Scholes por bisseção para revelar a IV embutida na APR
    que a Binance oferece no Dual Investment.

    No Dual Investment da Binance:
      - Buy Low (Short Put): o usuário deposita USDT (no strike K) e recebe
        rendimento `apr_pct * (dte / 365) * K`.
      - Sell High (Covered Call): o usuário deposita cripto (no spot S) e
        recebe `apr_pct * (dte / 365) * S`.
    """
    if spot <= 0 or strike <= 0 or dte_days <= 0 or apr_pct <= 0:
        return None
    base = strike if kind.upper().startswith("P") else spot
    target_price = base * (apr_pct / 100.0) * (dte_days / DAYS_PER_YEAR)
    # valor intrínseco mínimo
    intrinsic = max(0.0, strike - spot) if kind.upper().startswith("P") else max(0.0, spot - strike)
    if target_price <= intrinsic:
        return 1.0

    lo, hi = 1.0, 300.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        p = bs_option(spot, strike, dte_days, mid, kind)["price_usd"]
        if p < target_price:
            lo = mid
        else:
            hi = mid
    return round(0.5 * (lo + hi), 2)


# ------------------------------------------------------------------ Checklist de 11 Itens
def checklist_11(read: dict, strategy_type: str, settings: dict,
                 open_portfolio: dict | None = None,
                 carry_apr_net: float | None = None) -> dict:
    """Checklist do trader de volatilidade (11 itens, nota 0–10, selo A/B/C).

    Herdeiro direto do `trader_checklist` do FutAnalytics (PLANO_SIGMADESK seção 3.13).
    Regra de ouro inegociável: Selo C (< 6.0) NUNCA entra na carteira.
    """
    rv = read.get("realized") or {}
    vrp = read.get("vrp") or {}
    ts = read.get("term_structure") or []
    fw = read.get("forward_variance") or []
    sk = read.get("skew") or {}
    ou = read.get("vol_forecast") or {}
    jumps = rv.get("jumps") or {}
    pf = open_portfolio or {}

    items = []

    def add(num: int, name: str, status: str, detail: str):
        pts = 2 if status == "ok" else (1 if status == "warn" else 0)
        items.append({"n": num, "item": name, "status": status, "pts": pts, "detail": detail})

    # 1. Amostra efetiva do RV (n_eff)
    n_eff = rv.get("n_eff") or 0.0
    if n_eff >= 35:
        add(1, "Amostra efetiva de RV", "ok", f"n_eff = {n_eff:.1f} barras ponderadas (robusto)")
    elif n_eff >= 18:
        add(1, "Amostra efetiva de RV", "warn", f"n_eff = {n_eff:.1f} barras (moderado)")
    else:
        add(1, "Amostra efetiva de RV", "bad", f"n_eff = {n_eff:.1f} barras (amostra curta)")

    # 2. Qualidade do insumo (Yang-Zhang / Garman-Klass vs close-to-close)
    if rv.get("range_used") and rv.get("rv_yang_zhang") is not None:
        add(2, "Qualidade do estimador (Amplitude)", "ok",
            f"Yang-Zhang ativo ({rv.get('rv_yang_zhang')}%, peso {rv.get('range_weight_eff')})")
    elif rv.get("range_used"):
        add(2, "Qualidade do estimador (Amplitude)", "warn", "Parkinson/GK ativo, sem Yang-Zhang completo")
    else:
        add(2, "Qualidade do estimador (Amplitude)", "bad", "Apenas close-to-close (ruidoso)")

    # 3. Divergência Modelo × Mercado (sinal claro)
    vrp_val = vrp.get("vrp")
    if strategy_type == "carry_delta_neutral":
        net = carry_apr_net if carry_apr_net is not None else 0.0
        if net >= 8.0:
            add(3, "Prêmio de Carry / Basis vs Custos", "ok", f"Carry líquido +{net:.2f}% a.a. após taxas Binance")
        elif net >= 3.5:
            add(3, "Prêmio de Carry / Basis vs Custos", "warn", f"Carry líquido +{net:.2f}% a.a. (moderado)")
        else:
            add(3, "Prêmio de Carry / Basis vs Custos", "bad", f"Carry líquido {net:.2f}% a.a. (não paga o atrito)")
    elif strategy_type == "short_vol_dual":
        if vrp_val is not None and vrp_val >= 3.0:
            add(3, "Divergência IV × RV (VRP p/ Venda)", "ok", f"VRP +{vrp_val:.2f} pts (prêmio gordo)")
        elif vrp_val is not None and vrp_val >= 1.0:
            add(3, "Divergência IV × RV (VRP p/ Venda)", "warn", f"VRP +{vrp_val:.2f} pts (magro, exija APR alta)")
        else:
            add(3, "Divergência IV × RV (VRP p/ Venda)", "bad",
                f"VRP {vrp_val if vrp_val is not None else 0:.2f} pts — PROIBIDO vender prêmio barato")
    elif strategy_type == "long_vol_options":
        if vrp_val is not None and vrp_val <= -1.5:
            add(3, "Desconto de IV × RV (Compra de Vol)", "ok",
                f"VRP {vrp_val:.2f} pts (IV barata abaixo da RV prevista)")
        elif vrp_val is not None and vrp_val <= 1.0:
            add(3, "Desconto de IV × RV (Compra de Vol)", "warn",
                f"VRP {vrp_val:.2f} pts (neutro; só compre antes de catalisador)")
        else:
            add(3, "Desconto de IV × RV (Compra de Vol)", "bad",
                f"VRP +{vrp_val:.2f} pts (IV cara — comprar opção aqui sangra theta)")
    else:  # vol_target_futures
        rv_now = rv.get("rv") or 45.0
        if 20.0 <= rv_now <= 55.0:
            add(3, "Regime de Volatilidade p/ Vol-Targeting", "ok", f"RV {rv_now:.1f}% (regime limpo p/ dimensionar)")
        elif rv_now <= 75.0:
            add(3, "Regime de Volatilidade p/ Vol-Targeting", "warn", f"RV {rv_now:.1f}% (reduzir alavancagem)")
        else:
            add(3, "Regime de Volatilidade p/ Vol-Targeting", "bad", f"RV {rv_now:.1f}% (estresse extremo)")

    # 4. Z-Score do VRP / Posição Histórica
    z = vrp.get("zscore")
    if strategy_type == "short_vol_dual":
        if z is not None and z >= 0.4:
            add(4, "VRP Z-Score histórico", "ok", f"z = {z:+.2f} (acima da média histórica)")
        elif (z is not None and z >= -0.2) or (z is None and (vrp_val or 0) >= 3.0):
            add(4, "VRP Z-Score histórico", "warn", f"z = {z if z is not None else 's/hist'} (neutro)")
        else:
            add(4, "VRP Z-Score histórico", "bad", f"z = {z if z is not None else 'negativo'} (prêmio comprimido)")
    elif strategy_type == "long_vol_options":
        if (z is not None and z <= -0.4) or (vrp_val is not None and vrp_val < -1.0):
            add(4, "VRP Z-Score (Desconto de Vol)", "ok", f"Vol no piso relativo (VRP {vrp_val} pts)")
        elif vrp_val is not None and vrp_val <= 1.5:
            add(4, "VRP Z-Score (Desconto de Vol)", "warn", "Vol próxima do justo")
        else:
            add(4, "VRP Z-Score (Desconto de Vol)", "bad", "Vol cara na distribuição histórica")
    else:
        add(4, "Independência de Direção (Neutralidade)", "ok" if strategy_type == "carry_delta_neutral" else "warn",
            "Delta-neutro (imune à direção do preço)" if strategy_type == "carry_delta_neutral"
            else "Exposição direcional controlada por Vol-Targeting")

    # 5. Persistência / Clustering (curto vs longo horizonte)
    rv_s, rv_l = rv.get("rv_short") or 0.0, rv.get("rv_long") or 0.0
    ratio_sl = (rv_s / rv_l) if rv_l > 0 else 1.0
    if ratio_sl <= 1.12:
        add(5, "Clustering de Volatilidade (Curto vs Longo)", "ok",
            f"RV curto {rv_s:.1f}% ≤ longo {rv_l:.1f}% (regime estável)")
    elif ratio_sl <= 1.30:
        add(5, "Clustering de Volatilidade (Curto vs Longo)", "warn",
            f"RV curto {rv_s:.1f}% acelerando vs longo {rv_l:.1f}%")
    else:
        add(5, "Clustering de Volatilidade (Curto vs Longo)", "bad",
            f"Choque recente: RV curto {rv_s:.1f}% >> longo {rv_l:.1f}%")

    # 6. Estrutura a Termo e Forward Variance
    contango_n = sum(1 for f in fw if f.get("regime") == "contango")
    back_n = sum(1 for f in fw if f.get("regime") == "backwardation")
    if contango_n >= back_n and len(ts) >= 3:
        add(6, "Estrutura a Termo (Contango/Backwardation)", "ok",
            f"Curva normal em contango ({contango_n}/{len(fw)} janelas)")
    elif len(ts) >= 2:
        add(6, "Estrutura a Termo (Contango/Backwardation)", "warn",
            f"Curva mista/invertida ({back_n} janelas em backwardation)")
    else:
        add(6, "Estrutura a Termo (Contango/Backwardation)", "bad", "Curva sem profundidade")

    # 7. Skew 25-Delta / Risk Reversal
    rr = sk.get("risk_reversal")
    if rr is None:
        add(7, "Skew 25Δ e Assimetria", "warn", "Amostra parcial sem 25Δ líquido nas duas asas")
    elif abs(rr) <= 6.0:
        add(7, "Skew 25Δ e Assimetria", "ok", f"Risk reversal equilibrado ({rr:+.2f} pts — {sk.get('lean')})")
    else:
        add(7, "Skew 25Δ e Assimetria", "warn", f"Skew esticado ({rr:+.2f} pts — {sk.get('lean')})")

    # 8. Risco de Salto (Bipower Variation) e Cauda Gorda
    j_share = jumps.get("share") or 0.0
    kurt = rv.get("excess_kurtosis") or 0.0
    if j_share <= 0.18 and kurt <= 2.5:
        add(8, "Separação de Salto (Bipower Variation)", "ok",
            f"Fração de salto baixa ({j_share*100:.1f}%, curtose {kurt:.2f})")
    elif j_share <= 0.35:
        add(8, "Separação de Salto (Bipower Variation)", "warn",
            f"Saltos moderados ({j_share*100:.1f}% da variância, curtose {kurt:.2f})")
    else:
        add(8, "Separação de Salto (Bipower Variation)", "bad",
            f"Regime dominado por saltos ({j_share*100:.1f}% da variância)")

    # 9. Reversão Ornstein-Uhlenbeck
    if ou.get("ok") and ou.get("mean_reverting"):
        add(9, "Reversão à Média (Ornstein-Uhlenbeck)", "ok",
            f"κ = {ou.get('kappa_daily')}/dia (meia-vida {ou.get('half_life_days')}d → prev. {ou.get('rv_forecast')}%)")
    elif ou.get("ok"):
        add(9, "Reversão à Média (Ornstein-Uhlenbeck)", "warn", "Reversão lenta (κ baixo)")
    else:
        add(9, "Reversão à Média (Ornstein-Uhlenbeck)", "warn", "Série curta p/ OU completo; usando RV blend")

    # 10. Liquidez da Ponta & Execução na Binance
    n_liq = read.get("n_liquid") or 0
    if strategy_type in ("carry_delta_neutral", "vol_target_futures"):
        add(10, "Liquidez de Execução na Binance", "ok",
            "Book BTCUSDT/ETHUSDT Spot + Perp USDⓈ-M (spread ~0,01%, maior liquidez global)")
    elif n_liq >= 8:
        add(10, "Liquidez de Referência e Spread", "ok",
            f"{n_liq} strikes líquidos p/ auditar o preço da Binance sem escuro")
    else:
        add(10, "Liquidez de Referência e Spread", "warn",
            f"Apenas {n_liq} strikes líquidos na referência; exija margem extra na APR")

    # 11. Limite de Carteira e Perda em Estresse
    open_pos = pf.get("open_positions") or 0
    max_pos = settings.get("max_positions") or 4
    vega_over = pf.get("vega_acima_do_teto")
    if vega_over or open_pos >= max_pos:
        add(11, "Risco Agregado da Carteira", "bad",
            f"Carteira no limite ({open_pos}/{max_pos} posições, vega acima do teto={vega_over})")
    elif open_pos == 0:
        add(11, "Risco Agregado da Carteira", "ok", "Carteira limpa (0 posições abertas)")
    else:
        add(11, "Risco Agregado da Carteira", "warn",
            f"{open_pos}/{max_pos} posições abertas — considerar correlação BTC×ETH")

    total_pts = sum(i["pts"] for i in items)
    max_pts = len(items) * 2
    score_10 = round((total_pts / max_pts) * 10.0, 1)

    # Trava dura: vender vol com VRP negativo NUNCA pode receber selo A ou B.
    hard_block = None
    if strategy_type == "short_vol_dual" and (vrp_val is None or vrp_val < 0.5):
        hard_block = ("VRP negativo ou insuficiente: a volatilidade realizada prevista supera a "
                      "implícita. Vender opção / Dual Investment neste regime tem EV negativo.")
        score_10 = min(score_10, 4.5)
    elif strategy_type == "long_vol_options" and (vrp_val is not None and vrp_val > 2.0):
        hard_block = ("VRP positivo alto: opções estão caras. Comprar Call/Put a seco na Binance "
                      "Options agora paga prêmio inflado ao vendedor.")
        score_10 = min(score_10, 4.8)
    elif strategy_type == "carry_delta_neutral" and (carry_apr_net is not None and carry_apr_net <= 0):
        hard_block = "Carry líquido negativo após descontar taxas de montagem/desmontagem da Binance."
        score_10 = min(score_10, 4.5)

    grade = "A" if score_10 >= 7.8 else ("B" if score_10 >= 6.0 else "C")
    min_grade = settings.get("portfolio_min_grade", "B")
    allowed_grades = {"A": ("A",), "B": ("A", "B"), "C": ("A", "B", "C")}.get(min_grade, ("A", "B"))
    approved = (grade in allowed_grades) and (grade != "C") and (hard_block is None)

    return {
        "score": score_10,
        "grade": grade,
        "approved": approved,
        "hard_block": hard_block,
        "ok_count": sum(1 for i in items if i["status"] == "ok"),
        "warn_count": sum(1 for i in items if i["status"] == "warn"),
        "bad_count": sum(1 for i in items if i["status"] == "bad"),
        "items": items,
    }


# ------------------------------------------------------------------ Sizing por Estresse
def stress_sizing(spot: float, rv_annual_pct: float, iv_annual_pct: float,
                  settings: dict, strategy_type: str, edge_annual_pct: float,
                  dte_days: float = 30.0, vega_per_unit: float = 0.0) -> dict:
    """Dimensionamento de posição por PERDA EM CENÁRIO DE ESTRESSE (nunca por prêmio).

    Pergunta quanto a estrutura perde no pior entre 3 choques configuráveis:
      (a) IV sobe `stress_iv_bump` pontos (ex.: +3 pts) contra a posição
      (b) RV realizada vem `stress_rv_multiple` × a prevista (ex.: 2×)
      (c) Subjacente sofre gap adverso de `stress_gap_sigma` desvios-padrão
    """
    bankroll = max(float(settings.get("bankroll") or 1000.0), 1.0)
    kelly_frac = float(settings.get("kelly_fraction") or 0.25)
    cap_pct = float(settings.get("stake_cap_pct") or 3.0)
    iv_bump = float(settings.get("stress_iv_bump") or 3.0)
    rv_mult = float(settings.get("stress_rv_multiple") or 2.0)
    gap_sigma = float(settings.get("stress_gap_sigma") or 2.0)

    # Orçamento máximo de perda no estresse para 1 operação (em USD)
    max_stress_loss_budget = bankroll * (cap_pct / 100.0)
    daily_sigma_pct = (rv_annual_pct / math.sqrt(DAYS_PER_YEAR))
    period_sigma_pct = (rv_annual_pct * math.sqrt(max(dte_days, 1.0) / DAYS_PER_YEAR))

    # Choque (a), (b), (c) em % do notional da operação
    if strategy_type == "carry_delta_neutral":
        # No Cash & Carry 1x (50% Spot + 50% Perp 1x), delta é zero.
        # O estresse vem de: (a) funding virar negativo por 7 dias + custo de saída,
        # (b) descolamento temporário do basis (mark vs spot spike),
        # (c) necessidade de rebalancear margem se o preço subir > gap_sigma no período.
        shock_a_pct = 0.45 + 0.08 * iv_bump                     # spike de basis + saída
        shock_b_pct = 0.35 * rv_mult                            # atrito de funding adverso
        shock_c_pct = max(0.50, period_sigma_pct * gap_sigma * 0.08)
    elif strategy_type == "short_vol_dual":
        # No Dual Investment (Short Put ou Covered Call coberta), não há alavancagem,
        # mas se o ativo gapar `gap_sigma` além do strike (~5% OTM), a perda vs USDT é o tail move.
        otm_buffer_pct = 5.0
        shock_a_pct = iv_bump * 0.45                            # marcação a mercado de vega
        shock_b_pct = max(1.5, (period_sigma_pct * (rv_mult - 1.0)) * 0.65)
        shock_c_pct = max(2.0, period_sigma_pct * gap_sigma - otm_buffer_pct)
    elif strategy_type == "long_vol_options":
        # Compra seca de opção: perda máxima é 100% do prêmio alocado!
        # Aqui o notional recomendável é o próprio orçamento de risco (prêmio pago).
        shock_a_pct = 35.0
        shock_b_pct = 50.0
        shock_c_pct = 100.0                                     # perda total do prêmio se expirar OTM
    else:  # vol_target_futures
        # Stop técnico posicionado em 1.5 × σ_diária; no gap de estresse pode pular o stop
        shock_a_pct = daily_sigma_pct * 1.5
        shock_b_pct = daily_sigma_pct * rv_mult
        shock_c_pct = daily_sigma_pct * gap_sigma

    worst_shock_pct = max(shock_a_pct, shock_b_pct, shock_c_pct, 0.25)
    worst_driver = ("gap adverso de preço" if worst_shock_pct == shock_c_pct
                    else ("explosão de RV realizada" if worst_shock_pct == shock_b_pct
                          else "choque de IV / basis"))

    # Fator de convicção por Kelly fracionado sobre o edge anualizado
    edge_scale = min(1.0, max(0.25, (abs(edge_annual_pct) / 15.0) * (kelly_frac / 0.25)))
    target_stress_loss_usd = round(max_stress_loss_budget * edge_scale, 2)

    # Notional que produz exatamente `target_stress_loss_usd` no pior cenário
    raw_notional = target_stress_loss_usd / (worst_shock_pct / 100.0)
    # Teto prudencial por estratégia (% da banca total)
    max_notional_cap = bankroll * (0.65 if strategy_type == "carry_delta_neutral"
                                   else (0.30 if strategy_type == "short_vol_dual"
                                         else (cap_pct / 100.0 if strategy_type == "long_vol_options"
                                               else 0.25)))
    notional_usd = round(min(raw_notional, max_notional_cap), 2)
    actual_stress_loss = round(notional_usd * (worst_shock_pct / 100.0), 2)

    return {
        "bankroll_usd": round(bankroll, 2),
        "recommended_notional_usd": notional_usd,
        "notional_pct_bankroll": round((notional_usd / bankroll) * 100.0, 2),
        "qty_coin": round(notional_usd / spot, 6) if spot > 0 else 0.0,
        "max_loss_stress_usd": actual_stress_loss,
        "stress_loss_pct_bankroll": round((actual_stress_loss / bankroll) * 100.0, 2),
        "worst_scenario": worst_driver,
        "scenarios_loss_pct_of_notional": {
            "iv_bump_plus_pts": round(shock_a_pct, 2),
            "rv_multiple_spike": round(shock_b_pct, 2),
            "adverse_gap_sigma": round(shock_c_pct, 2),
        },
    }


# ------------------------------------------------------------------ Leitura Direcional + Momentum + Fluxo
def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    val = values[0]
    for x in values[1:]:
        val = x * k + val * (1.0 - k)
    return val


def _rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 2:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
    if avg_l < 1e-12:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def read_trend_and_momentum(candles: list[dict] | None, read: dict) -> dict:
    """Combina estrutura de preço (EMAs, ROC ajustado à volatilidade, RSI, canal 20d)
    com o posicionamento de opções (Skew 25Δ, Put/Call OI e Contango de Forwards)
    para entregar um diagnóstico direcional honesto e níveis táticos exatos.
    """
    ccy = read.get("ccy", "BTC")
    spot = float(read.get("spot") or (84003.06 if ccy == "BTC" else 3200.0))
    rv = read.get("realized") or {}
    sk = read.get("skew") or {}
    pcr = read.get("put_call") or {}
    fw = read.get("forward_variance") or []

    rv_now = float(rv.get("rv") or 39.0)
    yz_vol = float(rv.get("rv_yang_zhang") or rv_now)
    daily_sigma_pct = max(0.5, yz_vol / math.sqrt(DAYS_PER_YEAR))
    weekly_sigma_pct = daily_sigma_pct * math.sqrt(7.0)

    closes = [float(c["c"]) for c in (candles or []) if c.get("c") and float(c["c"]) > 0]
    highs = [float(c["h"]) for c in (candles or []) if c.get("h") and float(c["h"]) > 0]
    lows = [float(c["l"]) for c in (candles or []) if c.get("l") and float(c["l"]) > 0]

    ema9 = _ema(closes[-45:], 9) if len(closes) >= 10 else spot * 0.996
    ema21 = _ema(closes[-80:], 21) if len(closes) >= 21 else spot * 0.988
    ema50 = _ema(closes[-160:], 50) if len(closes) >= 50 else spot * 0.972
    ema200 = _ema(closes[-350:], 200) if len(closes) >= 120 else spot * 0.925

    def _roc(days_back: int) -> float:
        if len(closes) > days_back and closes[-1 - days_back] > 0:
            return round(((closes[-1] / closes[-1 - days_back]) - 1.0) * 100.0, 2)
        return 0.0

    roc_7d = _roc(7)
    roc_14d = _roc(14)
    roc_30d = _roc(29)
    vol_30d_move = max(1.0, rv_now * math.sqrt(30.0 / DAYS_PER_YEAR))
    z_mom_30d = round(roc_30d / vol_30d_move, 2)
    rsi14 = _rsi_wilder(closes[-60:], 14) if len(closes) >= 20 else 54.0

    ch_hi = max(highs[-20:]) if len(highs) >= 5 else spot * (1.0 + weekly_sigma_pct / 100.0)
    ch_lo = min(lows[-20:]) if len(lows) >= 5 else spot * (1.0 - weekly_sigma_pct / 100.0)
    ch_pos = round(((spot - ch_lo) / max(ch_hi - ch_lo, 1e-6)) * 100.0, 1)

    # Pontuação composta de tendência (-5 a +5)
    score = 0.0
    reasons = []
    if spot > ema21 and ema21 >= ema50:
        score += 2.0
        reasons.append(f"Preço ({spot:,.0f}) acima da EMA21 ({ema21:,.0f}) e EMA50 ({ema50:,.0f})")
    elif spot > ema21:
        score += 1.0
        reasons.append(f"Preço recuperou acima da EMA21 ({ema21:,.0f})")
    elif spot < ema21 and ema21 < ema50:
        score -= 2.0
        reasons.append(f"Preço ({spot:,.0f}) abaixo da EMA21 ({ema21:,.0f}) e EMA50 ({ema50:,.0f})")
    else:
        score -= 0.5
        reasons.append(f"Preço consolidando entre EMA21 ({ema21:,.0f}) e EMA50 ({ema50:,.0f})")

    if spot > ema200:
        score += 1.0
        reasons.append(f"Tendência macro primária de alta (acima da EMA200 em {ema200:,.0f})")
    else:
        score -= 1.0
        reasons.append(f"Abaixo da EMA200 ({ema200:,.0f}) — regime macro exige cautela")

    if z_mom_30d >= 0.4:
        score += 1.0
        reasons.append(f"Momentum de 30d positivo (+{roc_30d:.1f}%, z = +{z_mom_30d:.2f}σ)")
    elif z_mom_30d <= -0.4:
        score -= 1.0
        reasons.append(f"Momentum de 30d negativo ({roc_30d:.1f}%, z = {z_mom_30d:.2f}σ)")

    # Confirmação pelo mercado de derivativos (Skew 25Δ e Estrutura a Termo)
    rr = sk.get("risk_reversal")
    if rr is not None:
        if rr > 3.5:
            score -= 0.5
            reasons.append(f"Opções pagando caro por proteção de queda (Risk Reversal +{rr:.1f} pts)")
        elif rr < -1.5:
            score += 0.5
            reasons.append(f"Fluxo comprador de Calls (Risk Reversal {rr:.1f} pts)")

    contango_n = sum(1 for f in fw if f.get("regime") == "contango")
    if contango_n >= max(1, len(fw) // 2):
        score += 0.5
        reasons.append("Curva de futuros/forwards em Contango saudável (institucional absorvendo carry)")

    score = max(-5.0, min(5.0, round(score, 1)))
    if score >= 2.5:
        bias = "ALTA FORTE (Bullish)"
        stance = "COMPRA TÁTICA (Pullbacks) + CARRY DELTA-NEUTRO"
        direction_code = "LONG"
    elif score >= 1.0:
        bias = "ALTA MODERADA (Viés Comprador)"
        stance = "ACUMULAÇÃO EM SUPORTE / DUAL BUY LOW + CARRY"
        direction_code = "LONG_PULLBACK"
    elif score <= -2.0:
        bias = "BAIXISTA / DEFENSIVO (Bearish)"
        stance = "100% DELTA-NEUTRO (CARRY) + CAIXA USDT (Evitar Compra Seca)"
        direction_code = "DEFENSIVE_SHORT"
    else:
        bias = "NEUTRO / LATERAL (Range)"
        stance = "MERCADO DE RENDA: COLETAR FUNDING + COMPRAR SUPORTE / VENDER RESISTÊNCIA"
        direction_code = "RANGE"

    # Níveis operacionais calibrados pela volatilidade diária Yang-Zhang
    support_1sigma = round(spot * (1.0 - daily_sigma_pct / 100.0), 2)
    support_2sigma_wk = round(spot * (1.0 - weekly_sigma_pct / 100.0), 2)
    resist_1sigma = round(spot * (1.0 + daily_sigma_pct / 100.0), 2)
    resist_2sigma_wk = round(spot * (1.0 + weekly_sigma_pct / 100.0), 2)

    if direction_code in ("LONG", "LONG_PULLBACK"):
        entry_ideal = round(max(support_1sigma, min(spot, ema9)), 2)
        stop_price = round(entry_ideal * (1.0 - (1.5 * daily_sigma_pct) / 100.0), 2)
        tp1_price = round(entry_ideal * (1.0 + (2.0 * daily_sigma_pct) / 100.0), 2)
        tp2_price = round(entry_ideal * (1.0 + (3.5 * daily_sigma_pct) / 100.0), 2)
        tactical_call = (f"Comprar {ccy} na faixa US$ {entry_ideal:,.0f}–{spot:,.0f} | "
                         f"Stop em US$ {stop_price:,.0f} (-{1.5*daily_sigma_pct:.1f}%) | "
                         f"Alvo 1: US$ {tp1_price:,.0f} (+{2.0*daily_sigma_pct:.1f}%) · "
                         f"Alvo 2: US$ {tp2_price:,.0f} (+{3.5*daily_sigma_pct:.1f}%)")
    elif direction_code == "DEFENSIVE_SHORT":
        entry_ideal = round(min(resist_1sigma, max(spot, ema9)), 2)
        stop_price = round(entry_ideal * (1.0 + (1.5 * daily_sigma_pct) / 100.0), 2)
        tp1_price = round(entry_ideal * (1.0 - (2.0 * daily_sigma_pct) / 100.0), 2)
        tp2_price = round(entry_ideal * (1.0 - (3.5 * daily_sigma_pct) / 100.0), 2)
        tactical_call = (f"Evitar compra direcional; Hedge/Short tático em US$ {entry_ideal:,.0f} | "
                         f"Stop em US$ {stop_price:,.0f} | Alvo 1: US$ {tp1_price:,.0f} · Alvo 2: US$ {tp2_price:,.0f}")
    else:
        entry_ideal = support_1sigma
        stop_price = round(support_1sigma * (1.0 - (1.5 * daily_sigma_pct) / 100.0), 2)
        tp1_price = resist_1sigma
        tp2_price = resist_2sigma_wk
        tactical_call = (f"Operar extremos do canal: Comprar próximo ao suporte US$ {support_1sigma:,.0f} "
                         f"(Stop US$ {stop_price:,.0f}) e realizar na resistência US$ {resist_1sigma:,.0f}–{resist_2sigma_wk:,.0f}")

    return {
        "score": score,
        "bias": bias,
        "stance": stance,
        "direction_code": direction_code,
        "confidence_pct": round(min(92.0, 50.0 + abs(score) * 8.5), 1),
        "ema_9": round(ema9, 2),
        "ema_21": round(ema21, 2),
        "ema_50": round(ema50, 2),
        "ema_200": round(ema200, 2),
        "roc_7d_pct": roc_7d,
        "roc_14d_pct": roc_14d,
        "roc_30d_pct": roc_30d,
        "vol_adjusted_momentum_z": z_mom_30d,
        "rsi_14": rsi14,
        "channel_20d_low": round(ch_lo, 2),
        "channel_20d_high": round(ch_hi, 2),
        "channel_position_pct": ch_pos,
        "support_1sigma": support_1sigma,
        "support_2sigma_week": support_2sigma_wk,
        "resistance_1sigma": resist_1sigma,
        "resistance_2sigma_week": resist_2sigma_wk,
        "tactical_entry": entry_ideal,
        "tactical_stop": stop_price,
        "tactical_tp1": tp1_price,
        "tactical_tp2": tp2_price,
        "reward_risk_ratio": 2.33,
        "tactical_summary": tactical_call,
        "reasons": reasons,
    }


# ------------------------------------------------------------------ Construção da Mesa Binance por Moeda
def build_coin_desk(read: dict, funding_rows: list[dict], funding_source: str,
                    settings: dict, open_portfolio: dict | None = None,
                    candles: list[dict] | None = None) -> dict:
    """Constrói o plano completo da Mesa Binance para uma moeda (BTC ou ETH)."""
    ccy = read.get("ccy", "BTC")
    spot = float(read.get("spot") or (84003.06 if ccy == "BTC" else 3200.0))
    rv = read.get("realized") or {}
    vrp = read.get("vrp") or {}
    ts = read.get("term_structure") or []
    ou = read.get("vol_forecast") or {}

    iv30 = float(read.get("iv_30d") or vrp.get("iv_30d") or 35.0)
    rv_now = float(rv.get("rv") or 39.0)
    rv_fc = float(ou.get("rv_forecast") or vrp.get("rv_forecast") or rv_now)
    # Blend herdado do FutAnalytics: 25% modelo (OU/Yang-Zhang), 75% mercado (IV30)
    mw = float(settings.get("model_weight") or 0.25)
    blended_vol = (mw * rv_fc) + ((1.0 - mw) * iv30)
    vrp_pts = round(iv30 - rv_fc, 2)

    # --- 1. Funding & Basis Carry (Pilar 1: Cash-and-Carry Delta-Neutro na Binance)
    # Calcula taxa de funding anualizada + basis de 30d dos contratos forward
    t30 = min(ts, key=lambda t: abs(t["dte"] - 30.0)) if ts else None
    is_demo_opt = (read.get("sources") or {}).get("options") == "demo" or read.get("is_demo")
    if t30 and t30.get("forward") and not is_demo_opt:
        fwd30 = float(t30["forward"])
        dte30 = max(float(t30.get("dte") or 30.0), 1.0)
    else:
        # Âncora real de 26/set/2026 (Deribit 30OCT26 fwd 84.376 vs spot 84.003 -> ~5–11% a.a.)
        dte30 = 30.0
        annual_basis_anchor = 0.0985 if ccy == "BTC" else 0.0860
        fwd30 = spot * (1.0 + annual_basis_anchor * (dte30 / DAYS_PER_YEAR))
    basis_pct = ((fwd30 - spot) / spot) * 100.0
    basis_ann_pct = round(basis_pct * (DAYS_PER_YEAR / dte30), 2)

    if funding_rows:
        recent_f = [float(r.get("funding_8h") or 0.0) for r in funding_rows[-90:] if r.get("funding_8h") is not None]
        last_f8h = recent_f[-1] if recent_f else 0.0001
        avg_f8h = sum(recent_f) / len(recent_f) if recent_f else last_f8h
    else:
        # Se não veio série de funding separada, infere do carry implícito na curva forward
        avg_f8h = max(0.00008, (basis_ann_pct / 100.0) / (3.0 * DAYS_PER_YEAR))
        last_f8h = avg_f8h

    funding_ann_pct = round(avg_f8h * 3.0 * DAYS_PER_YEAR * 100.0, 2)
    gross_carry_apr = round(max(funding_ann_pct, basis_ann_pct), 2)
    vehicle_carry = ("Futuro Trimestral / Delivery (Basis Fixo)"
                     if basis_ann_pct >= funding_ann_pct
                     else "Perpétuo USDⓈ-M (Coleta de Funding a cada 8h)")
    rt_cost = BINANCE_FEES["carry_roundtrip_maker_bnb_pct"]
    net_carry_30d_apr = round(gross_carry_apr - rt_cost * (DAYS_PER_YEAR / 30.0), 2)
    daily_gross_yield = gross_carry_apr / DAYS_PER_YEAR
    breakeven_days = round(rt_cost / daily_gross_yield, 1) if daily_gross_yield > 0.001 else 999.0

    chk_carry = checklist_11(read, "carry_delta_neutral", settings, open_portfolio, net_carry_30d_apr)
    sz_carry = stress_sizing(spot, rv_fc, iv30, settings, "carry_delta_neutral",
                             max(net_carry_30d_apr, 1.0), dte_days=30.0)
    spot_leg_usd = round(sz_carry["recommended_notional_usd"] * 0.5, 2)
    perp_leg_usd = round(sz_carry["recommended_notional_usd"] * 0.5, 2)

    carry_plan = {
        "id": "spot_perp_carry",
        "rank_pillar": "Pilar 1 — Base de Consistência (60–70% do Capital)",
        "title": f"Cash-and-Carry Delta-Neutro ({ccy} Spot + Short 1x {ccy}USDT)",
        "binance_market": "Binance Spot + Binance Futures (USDⓈ-M / COIN-M)",
        "preferred_instrument": vehicle_carry,
        "funding_source": funding_source,
        "funding_8h_pct": round(last_f8h * 100.0, 4),
        "funding_ann_pct": funding_ann_pct,
        "forward_30d": round(fwd30, 2),
        "basis_30d_ann_pct": basis_ann_pct,
        "gross_apr_pct": gross_carry_apr,
        "binance_roundtrip_fee_pct": rt_cost,
        "net_apr_30d_pct": net_carry_30d_apr,
        "breakeven_days": breakeven_days,
        "delta_exposure": 0.0,
        "execution_steps": [
            f"1. Compre US$ {spot_leg_usd:.2f} de {ccy} no mercado Spot da Binance ({ccy}USDT ou {ccy}FDUSD c/ taxa maker).",
            f"2. Transfira a outra metade (US$ {perp_leg_usd:.2f} em USDT) ou o próprio {ccy} (Multi-Assets Mode / COIN-M) para a carteira de Futuros.",
            f"3. Abra venda (Short 1x) de mesma quantidade ({round(spot_leg_usd/spot, 5)} {ccy}) no {vehicle_carry}.",
            f"4. Segure por pelo menos {max(breakeven_days, 3.0):.0f} dias para diluir o custo de entrada/saída ({rt_cost}%) e embolsar +{net_carry_30d_apr:.2f}% a.a. sem risco direcional.",
        ],
        "checklist": chk_carry,
        "sizing": sz_carry,
    }

    # --- 2. Venda Coberta de Volatilidade na Binance (Dual Investment: Short Put / Covered Call)
    step = 1000.0 if ccy == "BTC" else 50.0
    put_strike_14d = round((spot * 0.945) / step) * step
    call_strike_14d = round((spot * 1.055) / step) * step
    dte_dual = 14.0

    # Precificação na IV de mercado vs RV prevista pelo modelo
    put_mkt = bs_option(spot, put_strike_14d, dte_dual, iv30, "P")
    put_mod = bs_option(spot, put_strike_14d, dte_dual, rv_fc, "P")
    call_mkt = bs_option(spot, call_strike_14d, dte_dual, iv30, "C")
    call_mod = bs_option(spot, call_strike_14d, dte_dual, rv_fc, "C")

    chk_dual = checklist_11(read, "short_vol_dual", settings, open_portfolio)
    sz_dual = stress_sizing(spot, rv_fc, iv30, settings, "short_vol_dual",
                            max(vrp_pts * 2.5, 0.5), dte_days=dte_dual, vega_per_unit=put_mkt["vega"])

    dual_plan = {
        "id": "dual_investment_vrp",
        "rank_pillar": "Pilar 2A — Venda Coberta de Volatilidade (Só quando VRP > +3 pts)",
        "title": f"Binance Dual Investment Auditado ({ccy} Buy Low / Sell High)",
        "binance_market": "Binance Earn → Dual Investment (Short Put / Covered Call coberta)",
        "why_not_raw_options": (
            "Na Binance Options (eapi), o varejo comum é bloqueado de lançar (vender) opções "
            "a descoberto. O Dual Investment permite vender Put coberta em USDT ou Call coberta "
            "em cripto, mas exige auditar se a APR oferecida paga acima da IV justa."
        ),
        "vrp_pts": vrp_pts,
        "iv_30d_pct": round(iv30, 2),
        "rv_forecast_pct": round(rv_fc, 2),
        "blended_vol_pct": round(blended_vol, 2),
        "buy_low_short_put": {
            "dte_days": dte_dual,
            "strike": put_strike_14d,
            "distance_otm_pct": round(((spot - put_strike_14d) / spot) * 100.0, 2),
            "fair_market_apr_pct": put_mkt["apr_equiv_pct"],
            "model_cost_apr_pct": put_mod["apr_equiv_pct"],
            "min_acceptable_apr_pct": round(max(put_mkt["apr_equiv_pct"] * 0.92,
                                                put_mod["apr_equiv_pct"] * 1.15), 2),
            "prob_exercise": put_mod["prob_itm"],
            "breakeven_spot": round(put_strike_14d - put_mkt["price_usd"], 2),
        },
        "sell_high_covered_call": {
            "dte_days": dte_dual,
            "strike": call_strike_14d,
            "distance_otm_pct": round(((call_strike_14d - spot) / spot) * 100.0, 2),
            "fair_market_apr_pct": call_mkt["apr_equiv_pct"],
            "model_cost_apr_pct": call_mod["apr_equiv_pct"],
            "min_acceptable_apr_pct": round(max(call_mkt["apr_equiv_pct"] * 0.92,
                                                call_mod["apr_equiv_pct"] * 1.15), 2),
            "prob_exercise": call_mod["prob_itm"],
            "breakeven_spot": round(call_strike_14d + call_mkt["price_usd"], 2),
        },
        "checklist": chk_dual,
        "sizing": sz_dual,
    }

    # --- 3. Compra Tática de Convexidade na Binance Options (Só quando VRP < 0)
    atm_strike = round(spot / step) * step
    dte_opt = 14.0
    call_atm = bs_option(spot, atm_strike, dte_opt, iv30, "C")
    put_atm = bs_option(spot, atm_strike, dte_opt, iv30, "P")
    straddle_cost_usd = round(call_atm["price_usd"] + put_atm["price_usd"], 2)
    straddle_breakeven_move_pct = round((straddle_cost_usd / spot) * 100.0, 2)
    expected_move_model_pct = round((rv_fc / 100.0) * math.sqrt(dte_opt / DAYS_PER_YEAR) * 100.0, 2)

    chk_long_opt = checklist_11(read, "long_vol_options", settings, open_portfolio)
    sz_long_opt = stress_sizing(spot, rv_fc, iv30, settings, "long_vol_options",
                                max(-vrp_pts * 3.0, 0.5), dte_days=dte_opt)

    long_options_plan = {
        "id": "binance_options_convexity",
        "rank_pillar": "Pilar 2B — Compra de Convexidade (Só quando VRP < 0 / Vol Barata)",
        "title": f"Binance Options Long Straddle / Convexidade ({ccy} ATM {int(atm_strike)})",
        "binance_market": "Binance Options (eapi — Contratos Europeus liquidados em USDT)",
        "dte_days": dte_opt,
        "atm_strike": atm_strike,
        "straddle_cost_usd_per_coin": straddle_cost_usd,
        "breakeven_move_pct": straddle_breakeven_move_pct,
        "model_expected_1sigma_move_pct": expected_move_model_pct,
        "edge_move_diff_pct": round(expected_move_model_pct - straddle_breakeven_move_pct, 2),
        "checklist": chk_long_opt,
        "sizing": sz_long_opt,
    }

    # --- 4. Vol-Targeting em Perpétuos USDⓈ-M (Pilar 3: Controle de Alavancagem e Stop)
    trend = read_trend_and_momentum(candles, read)
    target_vol_pct = 25.0
    optimal_lev = round(min(2.0, max(0.25, target_vol_pct / max(rv_fc, 10.0))), 2)
    yz_vol = float(rv.get("rv_yang_zhang") or rv_now)
    daily_sigma_pct = round(yz_vol / math.sqrt(DAYS_PER_YEAR), 2)
    stop_distance_pct = round(daily_sigma_pct * 1.5, 2)

    chk_vt = checklist_11(read, "vol_target_futures", settings, open_portfolio)
    sz_vt = stress_sizing(spot, rv_fc, iv30, settings, "vol_target_futures",
                          8.0, dte_days=7.0)

    vol_target_plan = {
        "id": "futures_vol_target",
        "rank_pillar": "Pilar 3 — Tático em Futuros USDⓈ-M (Vol-Targeting s/ Alavancagem Cega)",
        "title": f"Swing Vol-Targeted em {ccy}USDT Perp ({trend['bias']} · Máx {optimal_lev}x)",
        "binance_market": "Binance Futures USDⓈ-M (Modo Isolado / Limite Maker)",
        "target_vol_annual_pct": target_vol_pct,
        "forecast_vol_annual_pct": round(rv_fc, 2),
        "max_recommended_leverage": optimal_lev,
        "daily_sigma_yang_zhang_pct": daily_sigma_pct,
        "technical_stop_pct": stop_distance_pct,
        "stop_long_price": round(spot * (1.0 - stop_distance_pct / 100.0), 2),
        "stop_short_price": round(spot * (1.0 + stop_distance_pct / 100.0), 2),
        "tactical_direction": trend["direction_code"],
        "tactical_entry": trend["tactical_entry"],
        "tactical_stop": trend["tactical_stop"],
        "tactical_tp1": trend["tactical_tp1"],
        "tactical_tp2": trend["tactical_tp2"],
        "tactical_summary": trend["tactical_summary"],
        "checklist": chk_vt,
        "sizing": sz_vt,
    }

    # --- Ranking Dinâmico: Qual é a melhor operação HOJE para esta moeda?
    candidates = [carry_plan, dual_plan, long_options_plan, vol_target_plan]
    candidates_sorted = sorted(
        candidates,
        key=lambda x: (1 if x["checklist"]["approved"] else 0, x["checklist"]["score"]),
        reverse=True,
    )
    best = candidates_sorted[0]

    # Alocação sugerida da Banca na Binance de acordo com o regime atual
    bankroll = float(settings.get("bankroll") or 1000.0)
    if vrp_pts >= 3.0 and chk_dual["approved"]:
        regime_label = "VRP RICO (IV >> RV) — Vender Vol Coberta + Carry Delta-Neutro"
        alloc = [
            {"bucket": "Cash-and-Carry Delta-Neutro (Spot + Perp 1x)", "pct": 55,
             "usd": round(bankroll * 0.55, 2), "status": "ATIVO"},
            {"bucket": "Dual Investment (Short Put / Covered Call)", "pct": 30,
             "usd": round(bankroll * 0.30, 2), "status": "ATIVO (VRP Rico)"},
            {"bucket": "Reserva de Liquidez / Margem USDT", "pct": 15,
             "usd": round(bankroll * 0.15, 2), "status": "COLCHÃO DE ESTRESSE"},
        ]
    elif vrp_pts < 0.0:
        regime_label = "VRP NEGATIVO (IV < RV) — Proibido Vender Vol; Foco em Carry Neutro + Convexidade"
        alloc = [
            {"bucket": "Cash-and-Carry Delta-Neutro (Spot + Perp/Trimestral 1x)", "pct": 70,
             "usd": round(bankroll * 0.70, 2), "status": "ATIVO (Motor Principal)"},
            {"bucket": "Dual Investment (Venda de Vol)", "pct": 0,
             "usd": 0.0, "status": "BLOQUEADO (VRP Negativo)"},
            {"bucket": "Binance Options (Compra Tática de Convexidade)", "pct": 5 if chk_long_opt["approved"] else 0,
             "usd": round(bankroll * (0.05 if chk_long_opt["approved"] else 0.0), 2),
             "status": "HABILITADO (Risco Limitado)" if chk_long_opt["approved"] else "AGUARDAR"},
            {"bucket": "Reserva de Liquidez / Caixa em USDT", "pct": 25 if chk_long_opt["approved"] else 30,
             "usd": round(bankroll * (0.25 if chk_long_opt["approved"] else 0.30), 2),
             "status": "PROTEÇÃO DE CAUDA"},
        ]
    else:
        regime_label = "VRP MAGRO / NEUTRO — Prioridade Total ao Carry Delta-Neutro"
        alloc = [
            {"bucket": "Cash-and-Carry Delta-Neutro (Spot + Perp 1x)", "pct": 65,
             "usd": round(bankroll * 0.65, 2), "status": "ATIVO (Motor Principal)"},
            {"bucket": "Dual Investment (Só se APR Binance > Mínima Justa)", "pct": 15,
             "usd": round(bankroll * 0.15, 2), "status": "SELETIVO"},
            {"bucket": "Reserva de Liquidez / Margem USDT", "pct": 20,
             "usd": round(bankroll * 0.20, 2), "status": "COLCHÃO DE ESTRESSE"},
        ]

    return {
        "ccy": ccy,
        "spot": round(spot, 2),
        "iv_30d": round(iv30, 2),
        "rv_now": round(rv_now, 2),
        "rv_forecast": round(rv_fc, 2),
        "vrp_pts": vrp_pts,
        "vrp_band": vrp.get("band"),
        "regime_summary": regime_label,
        "trend": trend,
        "best_today": {
            "id": best["id"],
            "title": best["title"],
            "binance_market": best["binance_market"],
            "grade": best["checklist"]["grade"],
            "score": best["checklist"]["score"],
            "approved": best["checklist"]["approved"],
            "recommended_notional_usd": best["sizing"]["recommended_notional_usd"],
            "max_loss_stress_usd": best["sizing"]["max_loss_stress_usd"],
        },
        "capital_allocation": alloc,
        "strategies": {
            "carry": carry_plan,
            "dual_investment": dual_plan,
            "long_options": long_options_plan,
            "vol_target": vol_target_plan,
        },
        "ranking": [
            {
                "id": c["id"],
                "title": c["title"],
                "market": c["binance_market"],
                "grade": c["checklist"]["grade"],
                "score": c["checklist"]["score"],
                "approved": c["checklist"]["approved"],
                "hard_block": c["checklist"]["hard_block"],
                "notional_usd": c["sizing"]["recommended_notional_usd"],
                "stress_loss_usd": c["sizing"]["max_loss_stress_usd"],
            }
            for c in candidates_sorted
        ],
    }


# ------------------------------------------------------------------ Bússola de Decisão: O Que Fazer Agora
def build_action_now(desks: list[dict], settings: dict) -> dict:
    """Sintetiza a leitura de todas as moedas e responde diretamente:
      1. Onde investir agora (BTC vs ETH, % da banca e quantos US$ em cada)
      2. O que fazer exatamente neste momento na Binance (ordens práticas 1, 2, 3)
      3. Níveis exatos de Entrada, Alvo e Stop Loss
      4. O que NÃO fazer agora (armadilhas ativas no regime atual)
    """
    bankroll = max(float(settings.get("bankroll") or 1000.0), 1.0)
    if not desks:
        return {"ok": False, "headline": "Sem moedas disponíveis no radar"}

    # Pontua cada moeda combinando Checklist + Carry líquido + Tendência + Eficiência de Vol
    scored = []
    for d in desks:
        carry_net = float(((d.get("strategies") or {}).get("carry") or {}).get("net_apr_30d_pct") or 0.0)
        chk_score = float((d.get("best_today") or {}).get("score") or 5.0)
        tr_score = float((d.get("trend") or {}).get("score") or 0.0)
        rv_fc = float(d.get("rv_forecast") or 45.0)
        # Eficiência Sharpe-like do carry + tendência ajustada à vol
        composite = round(chk_score * 1.2 + (carry_net / 2.5) + (tr_score * 0.6) - (rv_fc / 50.0), 2)
        scored.append((composite, d))

    scored.sort(key=lambda x: x[0], reverse=True)
    primary = scored[0][1]
    secondary = scored[1][1] if len(scored) > 1 else None

    p_ccy = primary["ccy"]
    p_spot = primary["spot"]
    p_carry = primary["strategies"]["carry"]
    p_dual = primary["strategies"]["dual_investment"]
    p_vt = primary["strategies"]["vol_target"]
    p_tr = primary["trend"]
    p_vrp = primary["vrp_pts"]

    # Comparativo claro BTC vs ETH
    if secondary:
        s_ccy = secondary["ccy"]
        s_carry = secondary["strategies"]["carry"]
        s_tr = secondary["trend"]
        why_winner = (
            f"{p_ccy} é o melhor ativo para liderar a carteira hoje (Score {scored[0][0]} vs {scored[1][0]} do {s_ccy}): "
            f"oferece Carry líquido de +{p_carry['net_apr_30d_pct']:.2f}% a.a. (contra +{s_carry['net_apr_30d_pct']:.2f}% a.a. em {s_ccy}), "
            f"volatilidade prevista de {primary['rv_forecast']:.1f}% (vs {secondary['rv_forecast']:.1f}% em {s_ccy}) "
            f"e estrutura de preço em '{p_tr['bias']}' (vs '{s_tr['bias']}' em {s_ccy})."
        )
    else:
        why_winner = f"{p_ccy} é o ativo único selecionado no radar com Carry líquido de +{p_carry['net_apr_30d_pct']:.2f}% a.a."

    # Divisão prática da banca (100% explicada em US$)
    if p_vrp >= 3.0 and p_dual["checklist"]["approved"]:
        # Regime de VRP rico: Carry + Dual Investment + Tático + Reserva
        w_carry_p = 0.45
        w_second = 0.25
        w_tactical = 0.15
        w_cash = 0.15
    else:
        # Regime de VRP negativo/magro: Carry domina + Acumulação/Swing Tático + Reserva maior
        w_carry_p = 0.50 if secondary else 0.65
        w_second = 0.20 if secondary else 0.0
        w_tactical = 0.15
        w_cash = round(1.0 - (w_carry_p + w_second + w_tactical), 2)

    usd_carry_p = round(bankroll * w_carry_p, 2)
    usd_second = round(bankroll * w_second, 2)
    usd_tactical = round(bankroll * w_tactical, 2)
    usd_cash = round(bankroll - usd_carry_p - usd_second - usd_tactical, 2)

    monthly_carry_p_usd = round(usd_carry_p * (p_carry["net_apr_30d_pct"] / 100.0) / 12.0, 2)
    annual_carry_p_usd = round(usd_carry_p * (p_carry["net_apr_30d_pct"] / 100.0), 2)

    orders = [
        {
            "step": 1,
            "priority": "AÇÃO #1 — RENDA BASE IMEDIATA (RISCO DIRECIONAL ZERO)",
            "asset": f"{p_ccy}USDT",
            "strategy_id": "cash_and_carry_perp",
            "direction": "neutral_carry",
            "grade": p_carry["checklist"]["grade"],
            "score": p_carry["checklist"]["score"],
            "pct_bankroll": round(w_carry_p * 100),
            "amount_usd": usd_carry_p,
            "where_in_binance": f"Binance Spot ({p_ccy}USDT) + Binance Futures ({p_carry['preferred_instrument']})",
            "what_to_do_now": (
                f"Comprar US$ {usd_carry_p/2:,.2f} em {p_ccy} Spot (@ ~US$ {p_spot:,.0f}) e abrir Short 1x "
                f"de US$ {usd_carry_p/2:,.2f} ({usd_carry_p/(2*p_spot):.5f} {p_ccy}) no Futuro/Perpétuo."
            ),
            "levels": (f"Entrada: Mercado/Maker (~US$ {p_spot:,.0f}) · Delta = 0 · "
                       f"Break-even de taxas: {p_carry['breakeven_days']:.1f} dias"),
            "expected_outcome": f"+{p_carry['net_apr_30d_pct']:.2f}% a.a. líquido (~US$ {monthly_carry_p_usd:.2f}/mês ou US$ {annual_carry_p_usd:.2f}/ano)",
            "stress_loss_usd": round(usd_carry_p * (p_carry["sizing"]["stress_loss_pct_bankroll"] / max(p_carry["sizing"]["notional_pct_bankroll"], 1.0)), 2),
            "entry_iv": primary["iv_30d"],
            "expected_yield_num": p_carry["net_apr_30d_pct"],
        }
    ]

    if secondary and usd_second > 0:
        s_ccy = secondary["ccy"]
        s_spot = secondary["spot"]
        s_carry = secondary["strategies"]["carry"]
        s_dual = secondary["strategies"]["dual_investment"]
        if p_vrp >= 3.0 and p_dual["checklist"]["approved"]:
            bl = p_dual["buy_low_short_put"]
            orders.append({
                "step": 2,
                "priority": "AÇÃO #2 — MULTIPLICADOR DE ALFA (VENDA COBERTA DE VOLATILIDADE)",
                "asset": f"{p_ccy}USDT",
                "strategy_id": "dual_buy_low_put",
                "direction": "short_vol",
                "grade": p_dual["checklist"]["grade"],
                "score": p_dual["checklist"]["score"],
                "pct_bankroll": round(w_second * 100),
                "amount_usd": usd_second,
                "where_in_binance": f"Binance Earn → Dual Investment → {p_ccy} Buy Low (Short Put em USDT)",
                "what_to_do_now": (
                    f"Subscrever US$ {usd_second:,.2f} em USDT no Strike US$ {bl['strike']:,.0f} "
                    f"(~{bl['distance_otm_pct']:.1f}% abaixo do spot, prazo ~{bl['dte_days']:.0f}d) "
                    f"SOMENTE SE a APR da tela for ≥ {bl['min_acceptable_apr_pct']:.1f}% a.a."
                ),
                "levels": f"Strike de compra: US$ {bl['strike']:,.0f} · Break-even se exercido: US$ {bl['breakeven_spot']:,.0f}",
                "expected_outcome": f"APR justa ≥ +{bl['min_acceptable_apr_pct']:.1f}% a.a. (ou compra {p_ccy} com {bl['distance_otm_pct']:.1f}% de desconto)",
                "stress_loss_usd": round(usd_second * 0.04, 2),
                "entry_iv": primary["iv_30d"],
                "expected_yield_num": bl["fair_market_apr_pct"],
            })
        else:
            s_month_usd = round(usd_second * (s_carry["net_apr_30d_pct"] / 100.0) / 12.0, 2)
            orders.append({
                "step": 2,
                "priority": f"AÇÃO #2 — DIVERSIFICAÇÃO DELTA-NEUTRA EM {s_ccy}",
                "asset": f"{s_ccy}USDT",
                "strategy_id": "cash_and_carry_perp",
                "direction": "neutral_carry",
                "grade": s_carry["checklist"]["grade"],
                "score": s_carry["checklist"]["score"],
                "pct_bankroll": round(w_second * 100),
                "amount_usd": usd_second,
                "where_in_binance": f"Binance Spot ({s_ccy}USDT) + Binance Futures ({s_carry['preferred_instrument']})",
                "what_to_do_now": (
                    f"Como o VRP está em {p_vrp:+.2f} pts (não venda opção barata!), aloque US$ {usd_second:,.2f} "
                    f"no Cash-and-Carry de {s_ccy} (US$ {usd_second/2:,.2f} Spot + US$ {usd_second/2:,.2f} Short 1x)."
                ),
                "levels": f"Entrada: ~US$ {s_spot:,.0f} · Delta = 0 · Break-even: {s_carry['breakeven_days']:.1f} dias",
                "expected_outcome": f"+{s_carry['net_apr_30d_pct']:.2f}% a.a. líquido (~US$ {s_month_usd:.2f}/mês sem risco de direção)",
                "stress_loss_usd": round(usd_second * 0.02, 2),
                "entry_iv": secondary["iv_30d"],
                "expected_yield_num": s_carry["net_apr_30d_pct"],
            })

    # Ação #3: Operação Tática Direcional / Range com Níveis Exatos
    lev = p_vt["max_recommended_leverage"]
    orders.append({
        "step": 3,
        "priority": f"AÇÃO #3 — POSIÇÃO TÁTICA DIRECIONAL ({p_tr['bias']})",
        "asset": f"{p_ccy}USDT",
        "strategy_id": "perp_vol_target",
        "direction": "long_vol" if p_tr["direction_code"].startswith("LONG") else "neutral_carry",
        "grade": p_vt["checklist"]["grade"],
        "score": p_vt["checklist"]["score"],
        "pct_bankroll": round(w_tactical * 100),
        "amount_usd": usd_tactical,
        "where_in_binance": f"Binance Spot ou Futures USDⓈ-M Isolado (Alavancagem máx: {lev}x)",
        "what_to_do_now": p_tr["tactical_summary"],
        "levels": (f"Entrada: US$ {p_tr['tactical_entry']:,.0f} · Stop Loss: US$ {p_tr['tactical_stop']:,.0f} · "
                   f"Alvo 1: US$ {p_tr['tactical_tp1']:,.0f} · Alvo 2: US$ {p_tr['tactical_tp2']:,.0f} (R:R {p_tr['reward_risk_ratio']}x)"),
        "expected_outcome": (f"Risco controlado no Stop: -US$ {round(usd_tactical * (p_vt['technical_stop_pct']/100.0) * lev, 2):.2f} · "
                             f"Ganho no Alvo 2: +US$ {round(usd_tactical * (p_vt['technical_stop_pct']*2.33/100.0) * lev, 2):.2f}"),
        "stress_loss_usd": round(usd_tactical * (p_vt["technical_stop_pct"] / 100.0) * max(lev, 1.0), 2),
        "entry_iv": primary["iv_30d"],
        "expected_yield_num": round(p_vt["technical_stop_pct"] * 2.33 * lev, 2),
    })

    # Ação #4: Reserva em USDT
    orders.append({
        "step": 4,
        "priority": "AÇÃO #4 — RESERVA DE LIQUIDEZ & MARGEM DE SEGURANÇA",
        "asset": "USDT / FDUSD",
        "strategy_id": "iron_condor",
        "direction": "neutral_carry",
        "grade": "A",
        "score": 9.5,
        "pct_bankroll": round(w_cash * 100),
        "amount_usd": usd_cash,
        "where_in_binance": "Binance Simple Earn Flexível (USDT) ou Colateral Livre em USDⓈ-M",
        "what_to_do_now": (
            f"Manter US$ {usd_cash:,.2f} líquidos em USDT para absorver qualquer gap de 2σ "
            f"nas pernas de Futuros sem risco de liquidação ou para comprar pânico se {p_ccy} testar "
            f"o suporte de 2σ semanal (US$ {p_tr['support_2sigma_week']:,.0f})."
        ),
        "levels": f"Gatilho de compra de pânico (2σ semanal): {p_ccy} em US$ {p_tr['support_2sigma_week']:,.0f}",
        "expected_outcome": "Imunidade contra chamadas de margem + munição para quedas extremas",
        "stress_loss_usd": 0.0,
        "entry_iv": primary["iv_30d"],
        "expected_yield_num": 0.0,
    })

    # O que NÃO fazer agora (regras de sobrevivência baseadas nos números atuais)
    avoid = []
    if p_vrp < 1.0:
        avoid.append(
            f"NÃO venda opções nem entre em Dual Investment com APR baixa agora: o VRP de {p_ccy} está em "
            f"{p_vrp:+.2f} pts (IV {primary['iv_30d']}% vs RV prevista {primary['rv_forecast']}%). "
            f"Só aceite Dual Investment se a APR na Binance passar de {p_dual['buy_low_short_put']['min_acceptable_apr_pct']}% a.a."
        )
    avoid.append(
        f"NÃO use alavancagem acima de {lev}x em {p_ccy}USDT: a volatilidade diária Yang-Zhang é de "
        f"{p_vt['daily_sigma_yang_zhang_pct']:.2f}%/dia; alavancagem de 10x–20x será violinada pelo ruído normal."
    )
    avoid.append(
        "NÃO opere altcoins ilíquidas fora de BTC e ETH: o spread e o beta de queda destroem o EV de longo prazo."
    )

    return {
        "ok": True,
        "bankroll_usd": round(bankroll, 2),
        "headline": (
            f"PRIORIDADE AGORA: Liderar com {p_ccy}USDT ({round(w_carry_p*100)}% em Cash-and-Carry Delta-Neutro a "
            f"+{p_carry['net_apr_30d_pct']:.2f}% a.a. + {round(w_tactical*100)}% Tático em '{p_tr['bias']}')"
        ),
        "winner_coin": p_ccy,
        "why_winner": why_winner,
        "market_stance": p_tr["stance"],
        "orders_now": orders,
        "avoid_now": avoid,
        "beginner_guide": build_beginner_guide(desks, settings),
    }


def build_beginner_guide(desks: list[dict], settings: dict) -> dict:
    """Traduz toda a inteligência quantitativa do SigmaDesk para um COMPLETO INICIANTE,
    sem jargões, com instruções exatas de onde clicar no aplicativo da Binance e
    valores calculados ao centavo.
    """
    bankroll = max(float(settings.get("bankroll") or 1000.0), 1.0)
    if not desks:
        return {"ok": False}

    primary = desks[0]
    for d in desks:
        if d.get("ccy") == "BTC":
            primary = d
            break

    ccy = primary["ccy"]
    spot = primary["spot"]
    tr = primary["trend"]
    carry = primary["strategies"]["carry"]
    dual = primary["strategies"]["dual_investment"]
    vt = primary["strategies"]["vol_target"]
    bl = dual["buy_low_short_put"]

    # Semáforo visual simples para iniciante
    if tr["score"] >= 1.0 and carry["net_apr_30d_pct"] > 4.0:
        light = "green"
        light_label = "🟢 SINAL VERDE — Mercado Favorável (Tendência Positiva + Renda Ativa)"
        plain_summary = (
            f"O motor analisou os últimos 400 dias e o mercado de opções: o {ccy} está em tendência de alta "
            f"(preço de US$ {spot:,.0f} acima das médias de 21, 50 e 200 dias) e os apostadores estão pagando "
            f"+{carry['net_apr_30d_pct']:.2f}% ao ano líquidos em taxas para quem financia o mercado. "
            f"A oscilação normal hoje é de ±{vt['daily_sigma_yang_zhang_pct']:.1f}% por dia — então compre apenas "
            f"nos preços de desconto indicados abaixo, nunca tudo de uma vez."
        )
    elif tr["score"] <= -1.5:
        light = "red"
        light_label = "🔴 SINAL DE DEFESA — Mercado em Correção (Proteger Dinheiro em Dólar/USDT)"
        plain_summary = (
            f"O preço do {ccy} (US$ {spot:,.0f}) perdeu as médias de curto prazo. Para o iniciante, "
            f"a regra agora é NÃO tentar adivinhar o fundo comprando de qualquer jeito: mantenha a maior "
            f"parte em Dólar (USDT) rendendo todo dia ou na Renda Blindada (onde a queda não te afeta)."
        )
    else:
        light = "yellow"
        light_label = "🟡 SINAL AMARELO — Mercado Lateral (Ganhar Renda Enquanto o Preço Anda de Lado)"
        plain_summary = (
            f"O {ccy} está andando de lado perto de US$ {spot:,.0f}. Nesse cenário, quem tenta fazer trade "
            f"rápido perde dinheiro pagando taxa. O lucro fácil está em deixar o dinheiro rendendo em Dólar "
            f"e deixar ordens de compra só lá embaixo nos preços de promoção."
        )

    # TRILHA 1: 100% Iniciante (Zero Futuros, Zero Alavancagem, Impossível ser Liquidado)
    t1_earn_usd = round(bankroll * 0.40, 2)
    t1_buy1_usd = round(bankroll * 0.40, 2)
    t1_dip_usd = round(bankroll - t1_earn_usd - t1_buy1_usd, 2)
    t1_profit_tp1 = round(t1_buy1_usd * ((tr["tactical_tp1"] - tr["tactical_entry"]) / tr["tactical_entry"]), 2)
    t1_profit_tp2 = round(t1_buy1_usd * ((tr["tactical_tp2"] - tr["tactical_entry"]) / tr["tactical_entry"]), 2)
    t1_loss_stop = round(t1_buy1_usd * ((tr["tactical_entry"] - tr["tactical_stop"]) / tr["tactical_entry"]), 2)

    track_1 = {
        "id": "nivel_1_spot",
        "badge": "🌱 NÍVEL 1 — COMEÇAR DO ZERO (RECOMENDADO P/ 1ª SEMANA)",
        "title": "Carteira Simples à Vista (Spot) + Poupança em Dólar (Zero Risco de Liquidação)",
        "who_for": "Para quem nunca operou ou quer dormir tranquilo sem usar Futuros nem Opções.",
        "risk_level": "Baixo / Controlado (Impossível quebrar a conta, pois não usa dinheiro emprestado)",
        "steps": [
            {
                "step": 1,
                "title": f"Colocar 40% (US$ {t1_earn_usd:.2f}) na 'Poupança em Dólar' da Binance",
                "where_to_click": "App Binance → Menu 'Earn' (ou 'Ganhos') → 'Simple Earn' → busque 'USDT' → escolha 'Flexível' → digite o valor e confirme.",
                "what_happens": (
                    f"Seus US$ {t1_earn_usd:.2f} ficam em Dólar (não caem se o Bitcoin cair), rendem juros em dólar "
                    f"todos os dias automaticamente e você pode resgatar em 1 segundo quando quiser."
                ),
            },
            {
                "step": 2,
                "title": f"Comprar 40% (US$ {t1_buy1_usd:.2f}) em {ccy} no Preço Inteligente (Ordem Limite)",
                "where_to_click": (
                    f"App Binance → botão 'Trades' (no rodapé) → aba 'Spot' → escolha '{ccy}/USDT' → "
                    f"mude a caixinha de 'Ordem a Mercado' para 'Ordem Limite' → no campo Preço digite "
                    f"{tr['tactical_entry']:.0f} → no campo Total digite {t1_buy1_usd:.2f} → toque em 'Comprar {ccy}'."
                ),
                "what_happens": (
                    f"Em vez de pagar caro na pressa, sua ordem compra {ccy} a US$ {tr['tactical_entry']:,.0f}. "
                    f"Assim que comprar, seu alvo de venda com lucro é US$ {tr['tactical_tp1']:,.0f} (+US$ {t1_profit_tp1:.2f} de lucro) "
                    f"até US$ {tr['tactical_tp2']:,.0f} (+US$ {t1_profit_tp2:.2f} de lucro). Se cair abaixo de "
                    f"US$ {tr['tactical_stop']:,.0f}, venda para limitar a perda a apenas US$ {t1_loss_stop:.2f}."
                ),
            },
            {
                "step": 3,
                "title": f"Deixar 20% (US$ {t1_dip_usd:.2f}) na 'Armadilha de Promoção' (Compra de Pânico)",
                "where_to_click": (
                    f"Na mesma tela 'Spot' ({ccy}/USDT) → 'Ordem Limite' → no campo Preço digite "
                    f"{tr['support_2sigma_week']:.0f} → no campo Total digite {t1_dip_usd:.2f} → 'Comprar {ccy}'."
                ),
                "what_happens": (
                    f"US$ {tr['support_2sigma_week']:,.0f} é o piso estatístico da semana (onde os desesperados vendem barato). "
                    f"Se o {ccy} der uma agulhada até lá enquanto você dorme, você compra na promoção; se não cair, seu dinheiro fica intacto."
                ),
            },
        ],
    }

    # TRILHA 2: Iniciante Renda com Desconto (Dual Investment auditado)
    t2_dual_usd = round(bankroll * 0.30, 2)
    t2_Yield_14d = round(t2_dual_usd * (bl["min_acceptable_apr_pct"] / 100.0) * (bl["dte_days"] / 365.0), 2)
    track_2 = {
        "id": "nivel_2_dual",
        "badge": "🛡️ NÍVEL 2 — GANHAR JUROS ALTOS PARA ESPERAR O DESCONTO",
        "title": "Investimento Duplo ('Comprar na Baixa' Auditado pelo Motor)",
        "who_for": "Para quem tem USDT na conta e quer ganhar juros altos enquanto espera o Bitcoin cair até um preço barato.",
        "risk_level": "Moderado (Sem alavancagem; o único risco é comprar o Bitcoin pelo preço escolhido se ele cair)",
        "steps": [
            {
                "step": 1,
                "title": "Abrir o Investimento Duplo (Dual Investment) no Modo Iniciante",
                "where_to_click": (
                    f"App Binance → 'Earn' → 'Investimento Duplo' (Dual Investment) → ative a chavinha "
                    f"'Modo Iniciante' no topo → escolha '{ccy}' → toque em 'Comprar na Baixa' (Compre {ccy} com USDT)."
                ),
                "what_happens": "Você vai escolher um Preço-Alvo abaixo do preço de hoje para comprar Bitcoin se ele cair.",
            },
            {
                "step": 2,
                "title": f"Conferir a Regra de Ouro do Motor para o Strike US$ {bl['strike']:,.0f} (~{bl['dte_days']:.0f} dias)",
                "where_to_click": (
                    f"Procure a data de vencimento de ~{bl['dte_days']:.0f} dias e o Preço-Alvo perto de US$ {bl['strike']:,.0f}. "
                    f"Olhe a porcentagem de APR verde na tela da Binance."
                ),
                "what_happens": (
                    f"O nosso motor calculou que a taxa justa mínima hoje é de {bl['min_acceptable_apr_pct']:.1f}% ao ano. "
                    f"SE a Binance estiver pagando {bl['min_acceptable_apr_pct']:.1f}% ou mais, aplique US$ {t2_dual_usd:.2f} "
                    f"(você ganha ~US$ {t2_Yield_14d:.2f} em {bl['dte_days']:.0f} dias). "
                    f"SE a Binance estiver pagando MENOS que {bl['min_acceptable_apr_pct']:.1f}%, NÃO entre (é cilada de preço; fique no Nível 1!)."
                ),
            },
        ],
    }

    # TRILHA 3: Renda Blindada "Papel de Banco" (Cash-and-Carry explicado sem jargão)
    t3_total = round(bankroll * 0.50, 2)
    t3_half = round(t3_total * 0.50, 2)
    t3_monthly = round(t3_total * (carry["net_apr_30d_pct"] / 100.0) / 12.0, 2)
    t3_yearly = round(t3_total * (carry["net_apr_30d_pct"] / 100.0), 2)
    track_3 = {
        "id": "nivel_3_carry",
        "badge": "🏦 NÍVEL 3 — RENDA BLINDADA (RECEBER TAXA A CADA 8 HORAS)",
        "title": f"Operação 'Papel de Banco' em {ccy} (+{carry['net_apr_30d_pct']:.2f}% a.a. sem depender se sobe ou cai)",
        "who_for": "Para quem já aprendeu a comprar no Spot e quer receber a taxa que os traders apressados pagam 3 vezes por dia.",
        "risk_level": "Baixíssimo Risco de Preço (Se o Bitcoin cair 20% ou subir 20%, seu valor em Dólar não muda!)",
        "steps": [
            {
                "step": 1,
                "title": f"Metade 1: Comprar US$ {t3_half:.2f} de {ccy} normal na carteira Spot",
                "where_to_click": f"App Binance → 'Trades' → 'Spot' → '{ccy}/USDT' → Comprar US$ {t3_half:.2f} de {ccy}.",
                "what_happens": f"Se o {ccy} subir, essa metade ganha dinheiro. Se o {ccy} cair, essa metade perde valor.",
            },
            {
                "step": 2,
                "title": f"Metade 2: Fazer o 'Seguro Inverso' de US$ {t3_half:.2f} nos Futuros em 1x",
                "where_to_click": (
                    f"App Binance → 'Futuros' (no rodapé) → 'USDⓈ-M' → escolha '{ccy}USDT Perpétuo' → "
                    f"no topo esquerdo ajuste a alavancagem para 'Isolado 1x' (NUNCA mais que 1x ou 2x!) → "
                    f"digite US$ {t3_half:.2f} e toque no botão vermelho 'Vender / Short'."
                ),
                "what_happens": (
                    f"Pronto! Agora você está travado: se o {ccy} cair US$ 50, você perde US$ 50 no Spot mas "
                    f"GANHA exatamente US$ 50 no Futuro 1x. Seu dinheiro em Dólar está blindado! "
                    f"E a cada 8 horas (05h, 13h e 21h), a Binance deposita a Taxa de Financiamento na sua conta "
                    f"(~US$ {t3_monthly:.2f}/mês ou US$ {t3_yearly:.2f}/ano líquidos). Segure por pelo menos "
                    f"{carry['breakeven_days']:.0f} dias para pagar a taxa de entrada e saída."
                ),
            },
        ],
    }

    glossary = [
        {"term": "USDT (Tether)", "meaning": "É o Dólar Digital (1 USDT = 1 Dólar). Quando seu dinheiro está em USDT, ele não sofre com a queda do Bitcoin."},
        {"term": "Mercado Spot (À Vista)", "meaning": "É a compra normal de criptomoeda (como comprar dólar numa casa de câmbio). Não tem dívida e nunca quebra sua conta."},
        {"term": "Ordem Limite", "meaning": "Você escolhe o preço exato que aceita pagar e deixa agendado. É mais barato e evita comprar no topo."},
        {"term": "Stop Loss (Preço de Proteção)", "meaning": "Um alarme automático: se a moeda cair abaixo daquele valor, ela vende sozinha para proteger você de um tombo maior."},
        {"term": "Taxa de Financiamento (Funding)", "meaning": "Taxa paga a cada 8h pelos apostadores apressados para quem faz a operação blindada (Nível 3)."},
        {"term": "VRP (Prêmio de Volatilidade)", "meaning": "Mede se os seguros/opções estão caros ou baratos hoje. Só vendemos no Nível 2 quando estão caros!"},
    ]

    golden_rules = [
        "1. NUNCA clique em alavancagem de 5x, 10x, 20x ou 50x: é assim que 95% dos iniciantes perdem tudo em uma tarde.",
        "2. Comece pelo Nível 1 na primeira semana até se acostumar com os botões do aplicativo da Binance.",
        "3. Só opere Bitcoin (BTC) e Ethereum (ETH): moedas pequenas ('memecoins') caem 80% sem aviso.",
        "4. Nunca coloque 100% do seu dinheiro em uma única compra: mantenha sempre 15% a 40% em Dólar (USDT) rendendo no Simple Earn.",
    ]

    return {
        "ok": True,
        "bankroll_usd": round(bankroll, 2),
        "traffic_light": light,
        "traffic_light_label": light_label,
        "plain_summary": plain_summary,
        "recommended_start_track": "nivel_1_spot",
        "tracks": [track_1, track_2, track_3],
        "glossary": glossary,
        "golden_rules": golden_rules,
    }


# ------------------------------------------------------------------ Auditor de Oferta da Binance
def audit_binance_offer(read: dict, settings: dict, offer: dict,
                        open_portfolio: dict | None = None) -> dict:
    """Audita uma oferta concreta vista na tela da Binance (Dual Investment ou Opção)
    contra a superfície institucional (Deribit) e a previsão de RV do SigmaDesk.
    """
    ccy = read.get("ccy", "BTC")
    spot = float(read.get("spot") or (84003.06 if ccy == "BTC" else 3200.0))
    iv_surface = float(read.get("iv_30d") or 35.0)
    rv_now = float((read.get("realized") or {}).get("rv") or 39.0)
    rv_fc = float((read.get("vol_forecast") or {}).get("rv_forecast") or rv_now)

    product = str(offer.get("product") or "buy_low_put").lower()
    kind = "P" if "put" in product or "buy_low" in product else "C"
    strike = float(offer.get("strike") or (spot * 0.95 if kind == "P" else spot * 1.05))
    dte_days = max(float(offer.get("dte_days") or 14.0), 0.5)
    binance_apr_pct = float(offer.get("binance_apr_pct") or 25.0)

    # 1. Preço e APR justa na superfície institucional (Deribit) e no Modelo (SigmaDesk)
    bs_mkt = bs_option(spot, strike, dte_days, iv_surface, kind)
    bs_mod = bs_option(spot, strike, dte_days, rv_fc, kind)

    # 2. IV embutida na oferta da Binance
    iv_binance = implied_vol_from_apr(spot, strike, dte_days, binance_apr_pct, kind) or 0.0
    vrp_offer = round(iv_binance - rv_fc, 2)
    haircut_vs_deribit = round(iv_binance - iv_surface, 2)

    # 3. EV em % anualizado e em USD para o notional dimensionado
    apr_diff_vs_model = round(binance_apr_pct - bs_mod["apr_equiv_pct"], 2)
    apr_diff_vs_market = round(binance_apr_pct - bs_mkt["apr_equiv_pct"], 2)

    # Simula leitura ajustada com a IV real que a Binance está pagando
    synthetic_read = {
        **read,
        "vrp": {**(read.get("vrp") or {}), "vrp": vrp_offer},
    }
    chk = checklist_11(synthetic_read, "short_vol_dual", settings, open_portfolio)
    sz = stress_sizing(spot, rv_fc, iv_binance, settings, "short_vol_dual",
                       max(apr_diff_vs_model, 0.5), dte_days=dte_days, vega_per_unit=bs_mkt["vega"])

    notional = sz["recommended_notional_usd"]
    ev_usd = round(notional * (apr_diff_vs_model / 100.0) * (dte_days / DAYS_PER_YEAR), 2)
    gross_yield_usd = round(notional * (binance_apr_pct / 100.0) * (dte_days / DAYS_PER_YEAR), 2)

    if vrp_offer < 1.0 or apr_diff_vs_model <= 0:
        verdict = "RECUSAR OFERTA (CILADA DE PREÇO)"
        reason = (f"A APR de {binance_apr_pct:.1f}% equivale a uma IV de apenas {iv_binance:.1f}%, "
                  f"abaixo da volatilidade realizada prevista pelo motor ({rv_fc:.1f}%). "
                  f"A Binance está pagando menos que o custo atuarial do risco de exercício.")
        approved = False
    elif haircut_vs_deribit < -4.5:
        verdict = "CAUTELA — SPREAD EMBUTIDO ALTO NA BINANCE"
        reason = (f"A oferta supera a RV prevista ({iv_binance:.1f}% vs {rv_fc:.1f}%), mas a Binance "
                  f"está embutindo um desconto de {abs(haircut_vs_deribit):.1f} pts de vol abaixo "
                  f"da superfície institucional da Deribit ({iv_surface:.1f}%).")
        approved = chk["approved"]
    else:
        verdict = "APROVADA — PRÊMIO RICO ACIMA DO CUSTO ATUARIAL"
        reason = (f"A APR de {binance_apr_pct:.1f}% embute uma IV de {iv_binance:.1f}% "
                  f"(VRP de +{vrp_offer:.2f} pts sobre a RV prevista de {rv_fc:.1f}%), "
                  f"gerando EV líquido positivo de +US$ {ev_usd:.2f} no ciclo de {dte_days:.0f}d.")
        approved = chk["approved"]

    return {
        "ccy": ccy,
        "product": "Buy Low (Short Put em USDT)" if kind == "P" else "Sell High (Covered Call)",
        "kind": kind,
        "spot": round(spot, 2),
        "strike": round(strike, 2),
        "dte_days": dte_days,
        "binance_apr_pct": round(binance_apr_pct, 2),
        "iv_embedded_in_binance_pct": iv_binance,
        "iv_deribit_reference_pct": round(iv_surface, 2),
        "rv_model_forecast_pct": round(rv_fc, 2),
        "vrp_of_offer_pts": vrp_offer,
        "haircut_vs_deribit_pts": haircut_vs_deribit,
        "fair_market_apr_pct": bs_mkt["apr_equiv_pct"],
        "model_break_even_apr_pct": bs_mod["apr_equiv_pct"],
        "apr_edge_vs_model_pct": apr_diff_vs_model,
        "apr_edge_vs_market_pct": apr_diff_vs_market,
        "prob_exercise": bs_mod["prob_itm"],
        "greeks": bs_mkt,
        "verdict": verdict,
        "approved": approved,
        "reason": reason,
        "expected_gross_yield_usd": gross_yield_usd,
        "expected_net_ev_usd": ev_usd,
        "checklist": chk,
        "sizing": sz,
    }
