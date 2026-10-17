"""Leitura de volatilidade — corte da Etapa 0.

Este módulo é a PONTE entre a camada de dados (provider.py) e o motor completo
que vem na Etapa 1/2. Ele existe por um motivo prático: provar, com número na
tela, que os dados grátis capturados sustentam a tese antes de construir o
motor em cima.

O que já está aqui é herança DIRETA do FutAnalytics, não reimplementação:

* DOIS HORIZONTES DE DECAIMENTO. `model.py` usava DECAY_LONG=0.012 (meia-vida
  ~58d) e DECAY_SHORT=0.06 (~11d) com FORM_WEIGHT=0.25 para separar força
  estrutural de forma recente. Aqui as MESMAS constantes separam volatilidade
  estrutural de volatilidade recente — porque em ambos os casos o problema é
  "estimar um parâmetro latente a partir de observações com pesos decrescentes
  no tempo". RiskMetrics usa meia-vida de ~11 dias; o valor que o app de
  futebol já tinha estava certo.

* ESTIMADOR DE MELHOR QUALIDADE SOBRE DADO RUIDOSO. O futebol trocava gols por
  xG (peso 0.65, modulado por cobertura). Aqui troca-se retorno
  close-to-close por estimadores de amplitude (Parkinson/Garman-Klass/
  Yang-Zhang), que são 5-8x mais eficientes para a mesma latente.

* SEPARAÇÃO DE SALTO. Dixon-Coles corrigia o viés do Poisson independente nos
  placares baixos; bipower variation separa aqui a variância de difusão da
  variância de salto, que é o viés conhecido da log-normal nas caudas.

Anualização: cripto negocia 365 dias/ano, NÃO 252. Errar isso subestima a vol
anual em ~5,7% (sqrt(365/252)=1.204 vs 1.0) — e todo o VRP sairia enviesado.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- parâmetros
# Herdados do PARAMS do FutAnalytics, com o vocabulário trocado.
PARAMS = {
    "DECAY_LONG": 0.012,        # meia-vida ~58 dias (vol estrutural)  <- valor original
    "DECAY_SHORT": 0.06,        # meia-vida ~11 dias (vol recente)     <- valor original
    "SHORT_WEIGHT": 0.25,       # peso do horizonte curto              <- FORM_WEIGHT
    "RANGE_WEIGHT": 0.65,       # peso do estimador de amplitude       <- XG_WEIGHT
    "RANGE_MIN_BARS": 20.0,     # barras c/ OHLC p/ cobertura total    <- XG_MIN_COVERAGE
    "REGIME_PRIOR_STRENGTH": 60.0,   # <- LEAGUE_PRIOR_STRENGTH
    "PRIOR_VOL": 45.0,          # vol anual (%) de longo prazo do BTC, âncora bayesiana
    "DAYS_PER_YEAR": 365.0,     # CRIPTO: 365, não 252
    "ZSCORE_WINDOW": 180,       # janela do z-score de VRP
    "JUMP_THRESHOLD": 4.0,      # |r| > 4σ_difusão = salto
}


def _p(name):
    return PARAMS[name]


# ---------------------------------------------------------------- returns
def log_returns(candles: list[dict]) -> list[dict]:
    """Retornos logarítmicos + insumos de amplitude. Ordem ascendente."""
    out = []
    prev_c = None
    for c in candles:
        o, h, l, cl = c.get("o"), c.get("h"), c.get("l"), c.get("c")
        if cl is None or cl <= 0:
            prev_c = cl or prev_c
            continue
        row = {"ts": c["ts"], "o": o, "h": h, "l": l, "c": cl,
               "v": c.get("v") or 0.0, "r": None, "hl": None, "co": None, "oc": None}
        if prev_c and prev_c > 0:
            row["r"] = math.log(cl / prev_c)
            row["oc"] = math.log(cl / (o or cl)) if o else 0.0
            # gap de abertura: overnight return (necessário para Yang-Zhang)
            row["gap"] = math.log((o or prev_c) / prev_c) if o else 0.0
        if h and l and h > 0 and l > 0:
            row["hl"] = math.log(h / l)
        if o and cl and o > 0:
            row["co"] = math.log(cl / o)
        out.append(row)
        prev_c = cl
    return out


# ---------------------------------------------------------------- RV: dois horizontes
def _ewma_var(rows: list[dict], decay: float, max_bars: int,
              value_key: str = "r") -> tuple[float, float]:
    """Variância EWMA com shrinkage bayesiano. Devolve (var_diária, n_efetivo).

    Estruturalmente idêntica a `horizon()` de TeamSample.strengths():
        w = exp(-decay * idade)
        média encolhida = (Σ w·x + prior·k) / (Σ w + k)
    """
    prior_var = (_p("PRIOR_VOL") / 100.0) ** 2 / _p("DAYS_PER_YEAR")
    k = _p("REGIME_PRIOR_STRENGTH") / _p("DAYS_PER_YEAR")
    total = 0.0
    wsum = 0.0
    n = 0.0
    recent = rows[-max_bars:]
    # idade em dias a partir da última barra
    last_ts = rows[-1]["ts"] if rows else 0
    for row in recent:
        x = row.get(value_key)
        if x is None:
            continue
        age_days = max(0.0, (last_ts - row["ts"]) / 86400000.0)
        w = math.exp(-decay * age_days)
        total += w * x * x
        wsum += w
        n += w
    var = (total + prior_var * k) / (wsum + k)
    return max(var, 1e-12), n


def realized_vol(candles: list[dict]) -> dict:
    """Volatilidade realizada: dois horizontes + estimadores de amplitude + salto.

    Devolve tudo em VOL ANUAL EM PORCENTAGEM (45.0 = 45%), que é a unidade em
    que a Deribit expressa mark_iv. Comparar unidades diferentes é o erro mais
    comum e mais silencioso nesse tipo de motor.
    """
    rows = log_returns(candles)
    valid = [r for r in rows if r["r"] is not None]
    if len(valid) < 8:
        return {"ok": False, "n": len(valid),
                "reason": "histórico curto demais (mínimo 8 barras)"}

    ann = math.sqrt(_p("DAYS_PER_YEAR"))
    def pct(daily_var: float) -> float:
        return math.sqrt(max(daily_var, 0.0)) * ann * 100.0

    # --- horizonte longo e curto sobre retorno close-to-close (o "gols brutos")
    var_long, n_long = _ewma_var(valid, _p("DECAY_LONG"), 400)
    var_short, _n_short = _ewma_var(valid, _p("DECAY_SHORT"), 40)
    w_short = _p("SHORT_WEIGHT")
    var_cc = (1 - w_short) * var_long + w_short * var_short

    # --- estimadores de amplitude (o "xG": mesma latente, menos ruído)
    rng = [r for r in valid[-120:] if r["hl"] is not None]
    n_range = float(len(rng))
    parkinson = gk = yz = None
    if n_range >= 5:
        ln2 = math.log(2.0)
        parkinson = sum(r["hl"] ** 2 for r in rng) / (4.0 * ln2 * n_range)
        if all(r["co"] is not None for r in rng):
            gk = sum(0.5 * r["hl"] ** 2 - (2 * ln2 - 1) * r["co"] ** 2
                     for r in rng) / n_range
            gk = max(gk, 1e-12)
        # Yang-Zhang: variância de overnight + abertura-fechamento + Rogers-Satchell
        gaps = [r.get("gap") for r in rng if r.get("gap") is not None]
        if len(gaps) >= 5:
            mg = sum(gaps) / len(gaps)
            var_oc = sum((g - mg) ** 2 for g in gaps) / (len(gaps) - 1)
            k_yz = 0.34 / (1.34 + (len(gaps) + 1) / (len(gaps) - 1))
            rs = 0.0
            n_rs = 0
            for r in rng:
                if r["hl"] is None or r["co"] is None or not r["o"]:
                    continue
                hh = math.log(r["h"] / r["o"])
                hl = math.log(r["l"] / r["o"])
                ch = math.log(r["c"] / r["o"])
                cl = math.log(r["c"] / r["o"])
                rs += hh * (hh - ch) + hl * (hl - cl)
                n_rs += 1
            var_rs = rs / n_rs if n_rs else 0.0
            yz = max(var_oc + k_yz * max(var_rs, 0.0) + (1 - k_yz) * max(var_rs, 0.0), 1e-12)

    # --- blend: exatamente a forma de `_blend_strength` (peso × cobertura)
    best_range = yz or gk or parkinson
    range_used = best_range is not None
    if range_used:
        cov = min(1.0, n_range / _p("RANGE_MIN_BARS"))
        w_range = _p("RANGE_WEIGHT") * cov
        var_final = (1 - w_range) * var_cc + w_range * best_range
    else:
        w_range = 0.0
        var_final = var_cc
    var_final = max(var_final, 1e-12)

    # --- separação de salto por bipower variation (robusta a saltos)
    jumps = {"n": 0, "intensity": 0.0, "share": 0.0, "diffusion_vol": None}
    rr = [r["r"] for r in valid if r["r"] is not None]
    if len(rr) >= 10:
        w = rr[-min(len(rr), 90):]
        bv = (math.pi / 2.0) * sum(abs(w[i]) * abs(w[i - 1]) for i in range(1, len(w))) / (len(w) - 1)
        rv_simple = sum(x * x for x in w) / len(w)
        var_jump = max(0.0, rv_simple - bv)
        sd_diff = math.sqrt(max(bv, 1e-12))
        n_jump = sum(1 for x in w if abs(x) > _p("JUMP_THRESHOLD") * sd_diff)
        jumps = {
            "n": n_jump,
            "intensity": round(n_jump / len(w), 4),
            "share": round(var_jump / rv_simple, 4) if rv_simple > 0 else 0.0,
            "diffusion_vol": round(pct(bv), 2),
            "jump_vol": round(pct(var_jump), 2),
        }

    # --- momentos (alimentam a correção de cauda da Etapa 2)
    win = rr[-min(len(rr), 120):]
    m = sum(win) / len(win)
    sd = math.sqrt(sum((x - m) ** 2 for x in win) / max(len(win) - 1, 1))
    skew = kurt = None
    if sd > 1e-12 and len(win) > 4:
        skew = round(sum(((x - m) / sd) ** 3 for x in win) / len(win), 3)
        kurt = round(sum(((x - m) / sd) ** 4 for x in win) / len(win) - 3.0, 3)

    return {
        "ok": True,
        "n": len(valid),
        "n_range": int(n_range),
        "rv_long": round(pct(var_long), 2),
        "rv_short": round(pct(var_short), 2),
        "rv_cc": round(pct(var_cc), 2),
        "rv_parkinson": round(pct(parkinson), 2) if parkinson else None,
        "rv_garman_klass": round(pct(gk), 2) if gk else None,
        "rv_yang_zhang": round(pct(yz), 2) if yz else None,
        "rv": round(pct(var_final), 2),
        "var_daily": var_final,
        "range_weight_eff": round(w_range, 3),
        "range_used": range_used,
        "n_eff": round(n_long, 1),
        "skew": skew,
        "excess_kurtosis": kurt,
        "jumps": jumps,
        "last_close": rows[-1]["c"] if rows else None,
        "last_ts": rows[-1]["ts"] if rows else None,
    }


# ---------------------------------------------------------------- IV: superfície
def _atm_for_expiry(options: list[dict], expiry: str) -> dict | None:
    """IV do strike mais próximo do forward daquele vencimento, só ponta líquida."""
    exp = [o for o in options if o["expiry"] == expiry and o["mark_iv"]]
    if not exp:
        return None
    liquid = [o for o in exp if o["liquid"]] or exp
    fwds = [o["underlying_price"] for o in exp if o.get("underlying_price")]
    fwd = sum(fwds) / len(fwds) if fwds else None
    if not fwd:
        return None
    # ATM = strike onde |call − put| é mínimo (paridade put-call) -> forward real
    by_strike: dict[float, dict] = {}
    for o in liquid:
        by_strike.setdefault(o["strike"], {})[o["kind"]] = o
    best, best_gap = None, None
    for k, sides in by_strike.items():
        if "C" in sides and "P" in sides:
            gap = abs((sides["C"].get("mid") or 0) - (sides["P"].get("mid") or 0))
        else:
            gap = abs(k - fwd)
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, k
    if best is None:
        return None
    sides = by_strike.get(best) or {}
    picks = [s for s in sides.values() if s.get("mark_iv")]
    if not picks:
        return None
    iv = sum(s["mark_iv"] for s in picks) / len(picks)
    return {
        "expiry": expiry,
        "dte": round(min(s["dte"] for s in picks), 3),
        "strike": best,
        "forward": round(fwd, 2),
        "atm_iv": round(iv, 3),
        "n_liquid": sum(1 for o in exp if o["liquid"]),
        "n_all": len(exp),
        "oi": round(sum(o.get("oi") or 0 for o in exp), 1),
        "put_iv": round(sides["P"]["mark_iv"], 3) if "P" in sides else None,
        "call_iv": round(sides["C"]["mark_iv"], 3) if "C" in sides else None,
    }


def term_structure(options: list[dict]) -> list[dict]:
    """Estrutura a termo de IV ATM por vencimento, ordenada por DTE."""
    expiries = sorted({o["expiry"] for o in options})
    out = [t for t in (_atm_for_expiry(options, e) for e in expiries) if t]
    out.sort(key=lambda t: t["dte"])
    return out


def forward_variance(ts: list[dict]) -> list[dict]:
    """Variância forward entre vencimentos consecutivos.

        σ²_fwd(t1,t2) = (σ2²·T2 − σ1²·T1) / (T2 − T1)

    É o que separa "a vol de 30 dias está alta" de "o mercado espera que a vol
    SUBA no segundo mês". Contango = mercado espera calma; backwardation =
    mercado espera estresse à frente.
    """
    out = []
    for i in range(1, len(ts)):
        a, b = ts[i - 1], ts[i]
        t1, t2 = a["dte"] / 365.0, b["dte"] / 365.0
        if t2 <= t1 or t2 - t1 < 1e-6:
            continue
        v1 = (a["atm_iv"] / 100.0) ** 2
        v2 = (b["atm_iv"] / 100.0) ** 2
        fwd_var = (v2 * t2 - v1 * t1) / (t2 - t1)
        fwd_vol = math.sqrt(max(fwd_var, 0.0)) * 100.0
        out.append({
            "from": a["expiry"], "to": b["expiry"],
            "dte_from": a["dte"], "dte_to": b["dte"],
            "fwd_vol": round(fwd_vol, 2),
            "vs_front": round(fwd_vol - a["atm_iv"], 2),
            "regime": ("backwardation" if fwd_vol < a["atm_iv"] - 0.5
                       else ("contango" if fwd_vol > a["atm_iv"] + 0.5 else "flat")),
        })
    return out


def interpolate_iv(ts: list[dict], target_dte: float) -> float | None:
    """IV interpolada em variação (não em DTE) — é assim que se interpola vol."""
    pts = [(t["dte"], t["atm_iv"]) for t in ts if t["dte"] > 0]
    if not pts:
        return None
    if len(pts) == 1:
        return pts[0][1]
    pts.sort()
    if target_dte <= pts[0][0]:
        return pts[0][1]
    if target_dte >= pts[-1][0]:
        return pts[-1][1]
    # interpola em VARIÂNCIA × tempo (total variance), que é linear sob difusão
    for i in range(1, len(pts)):
        d0, v0 = pts[i - 1]
        d1, v1 = pts[i]
        if d0 <= target_dte <= d1:
            tv0 = (v0 / 100.0) ** 2 * d0
            tv1 = (v1 / 100.0) ** 2 * d1
            if d1 == d0:
                return v0
            t = (target_dte - d0) / (d1 - d0)
            tv = tv0 + t * (tv1 - tv0)
            return math.sqrt(max(tv, 0.0) / target_dte) * 100.0
    return pts[-1][1]


def model_free_variance(options: list[dict], expiry: str,
                        risk_free: float = 0.0) -> dict | None:
    """Variância implícita model-free, fórmula do VIX/CBOE sobre a cadeia real.

        σ² = (2/T)·Σ (ΔK/K²)·e^{RT}·Q(K) − (1/T)·(F/K₀ − 1)²

    Q(K) usa o MEIO do bid/ask quando existe; sem um dos lados, usa mark_price.
    Opções sem preço nenhum são puladas (e contadas) — não inventamos preço.
    É o herdeiro de `de_vig_markets`: em vez de tirar a margem de três
    resultados, integra a superfície inteira sem assumir modelo de precificação.
    """
    exp = [o for o in options if o["expiry"] == expiry]
    if len(exp) < 6:
        return None
    dte = min((o["dte"] for o in exp if o["dte"] > 0), default=None)
    if not dte or dte <= 0:
        return None
    T = dte / 365.0
    fwds = [o["underlying_price"] for o in exp if o.get("underlying_price")]
    if not fwds:
        return None
    F = sum(fwds) / len(fwds)

    # agrega por strike: Q = mid de call e put (o VIX usa a média dos dois)
    per_strike: dict[float, list[float]] = {}
    skipped = 0
    for o in exp:
        q = o["mid"] if o["mid"] is not None else o.get("mark_price")
        if q is None or q <= 0:
            skipped += 1
            continue
        per_strike.setdefault(o["strike"], []).append(q)
    if len(per_strike) < 4:
        return None
    strikes = sorted(per_strike)
    q = {k: sum(v) / len(v) for k, v in per_strike.items()}

    # K0 = primeiro strike imediatamente abaixo do forward
    below = [k for k in strikes if k < F]
    K0 = below[-1] if below else strikes[0]

    total = 0.0
    used = 0
    for i, k in enumerate(strikes):
        if i == 0:
            dk = (strikes[1] - strikes[0]) / 2.0
        elif i == len(strikes) - 1:
            dk = (strikes[-1] - strikes[-2]) / 2.0
        else:
            dk = (strikes[i + 1] - strikes[i - 1]) / 2.0
        if dk <= 0 or k <= 0:
            continue
        total += (dk / (k * k)) * math.exp(risk_free * T) * q[k]
        used += 1
    var = (2.0 / T) * total - (1.0 / T) * ((F / K0) - 1.0) ** 2
    var = max(var, 0.0)
    return {
        "expiry": expiry,
        "dte": round(dte, 3),
        "T_years": round(T, 5),
        "forward": round(F, 2),
        "K0": K0,
        "strikes_used": used,
        "strikes_skipped": skipped,
        "implied_var": var,
        "implied_vol": round(math.sqrt(var) * 100.0, 3),
    }


def skew_25d(options: list[dict], expiry: str) -> dict | None:
    """Risk reversal 25-delta aproximado por moneyness (não temos delta no book).

    Proxy honesta: IV do put ~7% OTM menos IV do call ~7% OTM. Positivo = puts
    mais caros = mercado pagando por proteção de queda. É o item "skew/carry"
    do checklist.
    """
    exp = [o for o in options if o["expiry"] == expiry and o["mark_iv"] and o["liquid"]]
    if len(exp) < 6:
        return None
    fwds = [o["underlying_price"] for o in exp if o.get("underlying_price")]
    if not fwds:
        return None
    fwd = sum(fwds) / len(fwds)
    put_k, call_k = fwd * 0.93, fwd * 1.07
    puts = [o for o in exp if o["kind"] == "P"]
    calls = [o for o in exp if o["kind"] == "C"]
    if not puts or not calls:
        return None
    p = min(puts, key=lambda o: abs(o["strike"] - put_k))
    c = min(calls, key=lambda o: abs(o["strike"] - call_k))
    atm = min(exp, key=lambda o: abs(o["strike"] - fwd))
    rr = p["mark_iv"] - c["mark_iv"]
    bf = (p["mark_iv"] + c["mark_iv"]) / 2.0 - atm["mark_iv"]
    return {
        "expiry": expiry,
        "forward": round(fwd, 2),
        "put_strike": p["strike"], "call_strike": c["strike"],
        "put_iv": round(p["mark_iv"], 2), "call_iv": round(c["mark_iv"], 2),
        "atm_iv": round(atm["mark_iv"], 2),
        "risk_reversal": round(rr, 2),
        "butterfly": round(bf, 2),
        "lean": ("proteção de queda cara" if rr > 2 else
                 ("proteção de alta cara" if rr < -2 else "neutro")),
    }


def put_call_oi(options: list[dict], expiry: str | None = None) -> dict:
    """Fluxo/posicionamento (segmento E): OI de puts vs calls."""
    sel = [o for o in options if (expiry is None or o["expiry"] == expiry)]
    poi = sum(o.get("oi") or 0 for o in sel if o["kind"] == "P")
    coi = sum(o.get("oi") or 0 for o in sel if o["kind"] == "C")
    ratio = (poi / coi) if coi > 0 else None
    return {
        "expiry": expiry,
        "put_oi": round(poi, 1),
        "call_oi": round(coi, 1),
        "ratio": round(ratio, 3) if ratio else None,
        "lean": (None if ratio is None else
                 ("defensivo" if ratio > 1.15 else
                  ("complacente" if ratio < 0.85 else "equilibrado"))),
    }


# ---------------------------------------------------------------- VRP: o sinal mestre
def ou_forecast(rv_series: list[float], horizon_days: float = 30.0) -> dict:
    """Previsão de vol para FRENTE por reversão de Ornstein-Uhlenbeck.

        σ(T) = σ̄ + (σ_agora − σ̄)·e^(−κ·T)

    A literatura é explícita: RV trailing é retrovisor; quem vende prêmio precisa
    de estimativa prospectiva. κ é estimado por regressão de Δlog(σ) contra
    log(σ) − log(σ̄).
    """
    s = [x for x in rv_series if x and x > 0]
    if len(s) < 30:
        return {"ok": False, "reason": "série curta para estimar reversão"}
    sigma_bar = math.exp(sum(math.log(x) for x in s) / len(s))   # média geométrica
    num = den = 0.0
    for i in range(1, len(s)):
        x = s[i - 1] - sigma_bar
        y = s[i] - s[i - 1]
        num += x * y
        den += x * x
    kappa = 0.0
    if den > 1e-12:
        # y = -kappa*x  =>  kappa = -num/den  (por passo diário)
        kappa = max(0.0, -num / den)
    kappa = min(kappa, 1.0)
    now = s[-1]
    fc = sigma_bar + (now - sigma_bar) * math.exp(-kappa * horizon_days)
    return {
        "ok": True,
        "sigma_bar": round(sigma_bar, 2),
        "kappa_daily": round(kappa, 4),
        "half_life_days": round(math.log(2) / kappa, 1) if kappa > 1e-6 else None,
        "rv_now": round(now, 2),
        "rv_forecast": round(max(fc, 1.0), 2),
        "horizon_days": horizon_days,
        "mean_reverting": kappa > 0.01,
    }


def vrp_read(iv30: float | None, rv_now: float | None,
             rv_forecast: float | None,
             vrp_history: list[float] | None = None) -> dict:
    """VRP = σ_implícita − σ_prevista, com z-score e leitura acionável.

    Regra de leitura vinda da prática (e confirmada pela literatura de 2026):
        > 6 pts   prêmio muito rico      -> vendedor trabalha cheio
        3 a 6     saudável                -> tamanho padrão
        1 a 3     magro                   -> reduz, prefere risco definido
        0 a 1     marginal                -> exposição mínima ou fica de fora
        < 0       NEGATIVO                -> não venda prêmio; considere comprar
    """
    if iv30 is None or rv_now is None:
        return {"ok": False, "reason": "sem IV 30d ou sem RV"}
    base = rv_forecast if rv_forecast else rv_now
    vrp = iv30 - base
    vrp_trailing = iv30 - rv_now
    out = {
        "ok": True,
        "iv_30d": round(iv30, 2),
        "rv_now": round(rv_now, 2),
        "rv_forecast": round(base, 2) if rv_forecast else None,
        "vrp": round(vrp, 2),
        "vrp_trailing": round(vrp_trailing, 2),
        "ratio": round(iv30 / base, 3) if base > 0 else None,
    }
    hist = [x for x in (vrp_history or []) if x is not None]
    if len(hist) >= 30:
        w = hist[-_p("ZSCORE_WINDOW"):]
        m = sum(w) / len(w)
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / max(len(w) - 1, 1))
        out["zscore"] = round((vrp - m) / sd, 2) if sd > 1e-9 else None
        out["hist_mean"] = round(m, 2)
        out["hist_sd"] = round(sd, 2)
        out["hist_n"] = len(w)
        out["pct_positive"] = round(sum(1 for x in w if x > 0) / len(w), 3)
    if vrp > 6:
        out["band"], out["action"] = "muito rico", "vendedor trabalha com tamanho cheio"
    elif vrp > 3:
        out["band"], out["action"] = "saudável", "tamanho padrão"
    elif vrp > 1:
        out["band"], out["action"] = "magro", "reduz tamanho, prefere risco definido"
    elif vrp > 0:
        out["band"], out["action"] = "marginal", "exposição mínima ou fique de fora"
    else:
        out["band"], out["action"] = "NEGATIVO", "não venda prêmio; considere comprar vol"
    return out


# ---------------------------------------------------------------- leitura completa
def read_surface(options: list[dict], candles: list[dict],
                 dvol: list[dict] | None = None, ccy: str = "BTC") -> dict:
    """Empacota a leitura de volatilidade de uma moeda para o painel."""
    rv = realized_vol(candles)
    ts = term_structure(options)
    iv30 = interpolate_iv(ts, 30.0) if ts else None
    mfv = None
    if ts:
        # vencimento mais próximo de 30 dias
        target = min(ts, key=lambda t: abs(t["dte"] - 30.0))
        mfv = model_free_variance(options, target["expiry"])

    # série histórica de VRP: DVOL (IV) contra RV rolante das velas
    vrp_hist = []
    if dvol and rv.get("ok"):
        closes = {c["ts"]: c for c in candles}
        dvol_by_day = {int(d["ts"] // 86400000): d["c"] for d in dvol if d.get("c")}
        ts_sorted = sorted(closes)
        for i in range(60, len(ts_sorted)):
            day = int(ts_sorted[i] // 86400000)
            iv = dvol_by_day.get(day)
            if not iv:
                continue
            window = [closes[t] for t in ts_sorted[max(0, i - 30):i + 1]]
            r = realized_vol(window)
            if r.get("ok") and r.get("rv"):
                vrp_hist.append(iv - r["rv"])

    ou = ou_forecast([d["c"] for d in (dvol or []) if d.get("c")]) if dvol else {"ok": False}
    # previsão de RV a partir das velas realizadas (janela rolante de 30d)
    rv_series = []
    if rv.get("ok"):
        for i in range(60, len(candles) + 1):
            r = realized_vol(candles[max(0, i - 90):i])
            if r.get("ok") and r.get("rv"):
                rv_series.append(r["rv"])
    ou_rv = ou_forecast(rv_series, 30.0) if len(rv_series) >= 30 else {"ok": False}
    rv_forecast = ou_rv.get("rv_forecast") if ou_rv.get("ok") else None

    vrp = vrp_read(iv30, rv.get("rv"), rv_forecast, vrp_hist)

    fwds = forward_variance(ts)
    target_exp = ts[-1]["expiry"] if ts else None
    sk = skew_25d(options, min(ts, key=lambda t: abs(t["dte"] - 30.0))["expiry"]) if ts else None
    pcr = put_call_oi(options)

    return {
        "ccy": ccy,
        "ok": rv.get("ok", False) and bool(ts),
        "spot": rv.get("last_close"),
        "realized": rv,
        "term_structure": ts,
        "forward_variance": fwds,
        "model_free": mfv,
        "iv_30d": round(iv30, 2) if iv30 else None,
        "skew": sk,
        "put_call": pcr,
        "vol_forecast": ou_rv,
        "dvol_forecast": ou,
        "vrp": vrp,
        "vrp_history_n": len(vrp_hist),
        "n_options": len(options),
        "n_liquid": sum(1 for o in options if o.get("liquid")),
        "n_expiries": len({o["expiry"] for o in options}),
    }
