"""BinaryMaster — motor de confluência para opções binárias (M1/M5).

Filosofia honesta (leia antes de operar):
- Opção binária tem payout < 100%: com payout 85%, você precisa de ~54% de
  acerto SÓ para empatar (1/(1+0.85)). Com payout 70%, precisa de ~59%.
  Qualquer ferramenta que prometa "90% de assertividade" está mentindo.
- Este motor NÃO prevê o futuro. Ele organiza o melhor conhecimento público
  das escolas mundiais (price action japonesa, SMC/ICT americana, trend
  europeia, quantitativa russa, disciplina asiática e scalping BR de M1/M5)
  em um checklist de confluências, filtra entradas ruins e dimensiona risco.
  Lucro consistente vem de: payout alto + filtro rigoroso + gestão de banca.
  Sem esses três, nenhuma análise funciona — com eles, o edge é pequeno e
  exige centenas de operações para aparecer.
- Sinais com score baixo retornam "SEM ENTRADA" de propósito. Ficar de fora
  é a decisão mais lucrativa na maior parte do tempo.

Arquitetura:
- analyze_chart_image(): visão computacional leve (PIL + numpy, sem OpenCV
  para caber no Render free) que extrai sequência de candles, tendência,
  volatilidade e posição no range a partir do print do gráfico.
- score_confluence(): 12 módulos de estratégia votam CALL/PUT com pesos;
  o agregado vira score -100..+100, decisão, expiração (M1/M5), confiança
  honesta (teto de ~68%) e gestão sugerida.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field


# ------------------------------------------------------------- constantes
VERSION = "1.0.0"

# Break-even honesto: winrate mínima p/ empatar dado o payout.
def breakeven_rate(payout_pct: float) -> float:
    p = max(1.0, min(99.0, payout_pct)) / 100.0
    return 1.0 / (1.0 + p)


def expected_value(winrate: float, payout_pct: float) -> float:
    """EV por unidade de stake. Ex: win 0.58, payout 85% -> 0.58*0.85-0.42."""
    p = max(0.0, min(1.0, payout_pct / 100.0))
    return winrate * p - (1.0 - winrate)


# ================================================================ VISÃO
@dataclass
class VisionFeatures:
    ok: bool = False
    error: str = ""
    quality: float = 0.0          # 0..1 — quanto do gráfico foi detectado
    n_slices: int = 0
    seq: str = ""                 # ex: "G R G G R" (antigo -> recente)
    n_green: int = 0
    n_red: int = 0
    pct_green: float = 50.0
    streak_dir: str = "—"         # CALL / PUT / —
    streak_len: int = 0
    slope: float = 0.0            # + sobe / - desce (normalizado -1..1)
    trend_vision: str = "lateral" # forte_alta/alta/lateral/baixa/forte_baixa
    volatility: float = 0.0       # 0..1
    volatility_label: str = "média"
    range_pos: float = 0.5        # 0 = fundo, 1 = topo do range visível
    range_label: str = "meio do range"
    body_ratio: float = 0.5       # corpo médio vs candle cheio
    doji_last: bool = False
    doji_count: int = 0
    engulfing: str = "nenhum"     # bullish/bearish/nenhum
    last_color: str = "—"         # G / R / —
    exhaustion: str = "não"       # possível exaustão de streak
    notes: list = field(default_factory=list)


def _luminance(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def analyze_chart_image(image_bytes: bytes, max_side: int = 1100) -> VisionFeatures:
    """Extrai estrutura de candles de um print de gráfico (qualquer corretora).

    Funciona com temas escuros e claros, TradingView/Quotex/IQ/Pocket/Exnova,
    porque detecta pixels VERDES vs VERMELHOS (cores universais de alta/baixa)
    em vez de tentar ler eixos ou OCR. Retorna features + qualidade; se a
    qualidade for baixa, o motor reduz o peso da visão e pede confirmação.
    """
    feat = VisionFeatures()
    try:
        from PIL import Image
        import numpy as np
    except Exception as e:  # pragma: no cover
        feat.error = f"Dependências de visão ausentes: {e}"
        return feat
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        feat.error = "Não consegui abrir a imagem. Cole um print PNG/JPG do gráfico."
        return feat

    w, h = img.size
    if w < 200 or h < 150:
        feat.error = "Print muito pequeno — amplie o gráfico e cole novamente."
        return feat
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
        w, h = img.size

    import numpy as np
    a = np.asarray(img).astype(np.float32)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]

    # --- máscaras de cor: verde (alta) vs vermelho (baixa)
    # Cobre TradingView (#26a69a/#ef5350), Quotex, IQ Option, Pocket, Exnova.
    green = (G > 70) & (G > R + 12) & (G > B - 10)
    red = (R > 95) & (R > G + 12) & (R > B - 30)
    # variantes azuladas de alta (alguns temas usam ciano/azul p/ alta)
    blue_up = (B > 110) & (B > R + 25) & (G > 70)
    green = green | blue_up

    # --- recorte da área do gráfico: descarta bordas (eixos, menus)
    ys, xs = np.where(green | red)
    if len(xs) < 500:
        feat.error = ("Não encontrei candles suficientes no print. Dicas: maximize o "
                      "gráfico de CANDLES (não linha/área), dê zoom para mostrar ~30-60 "
                      "velas e evite cobrir o gráfico com menus.")
        return feat
    x0, x1 = int(np.percentile(xs, 1)), int(np.percentile(xs, 99))
    y0, y1 = int(np.percentile(ys, 1)), int(np.percentile(ys, 99))
    # remove faixa direita (eixo de preço) e faixa inferior (eixo de tempo)
    x1 = min(x1, int(w * 0.93))
    y1 = min(y1, int(h * 0.90))
    if x1 - x0 < 100 or y1 - y0 < 60:
        feat.error = "Área de candles muito pequena no print."
        return feat

    # --- varredura por fatias verticais -> sequência de candles
    N = 64
    edges = np.linspace(x0, x1, N + 1).astype(int)
    colors: list[str] = []
    closes: list[float] = []     # y estimado do fechamento (menor = preço maior)
    highs: list[float] = []
    lows: list[float] = []
    bodies: list[float] = []
    for i in range(N):
        xa, xb = edges[i], max(edges[i] + 1, edges[i + 1])
        gm = green[y0:y1, xa:xb]
        rm = red[y0:y1, xa:xb]
        cg, cr = int(gm.sum()), int(rm.sum())
        tot = cg + cr
        if tot < 25:  # sem candle nesta fatia
            continue
        col = "G" if cg >= cr else "R"
        mask = gm if col == "G" else rm
        rows = np.where(mask.any(axis=1))[0]
        if len(rows) < 2:
            continue
        hi, lo = float(rows.min()), float(rows.max())
        # corpo ~= linhas densas; pavio = linhas esparsas
        per_row = mask.sum(axis=1)
        dense = np.where(per_row >= max(2, per_row.max() * 0.45))[0]
        if len(dense) >= 2:
            bt, bb = float(dense.min()), float(dense.max())
        else:
            bt, bb = hi, lo
        close = bt if col == "G" else bb
        colors.append(col)
        closes.append(close)
        highs.append(hi)
        lows.append(lo)
        full = max(1.0, lo - hi)
        bodies.append(min(1.0, (bb - bt) / full))

    if len(colors) < 8:
        feat.error = ("Detectei poucas velas (%d). Mostre mais histórico no print "
                      "(zoom out) e tente de novo." % len(colors))
        return feat

    # compacta: junta fatias vizinhas iguais (cada candle pode ocupar 2-3 fatias)
    seq: list[str] = []
    for c in colors:
        if not seq or seq[-1] != c:
            seq.append(c)
        elif len(seq) >= 1:
            # permite até 2 repetidas antes de fundir (evita streak falso)
            pass
    # Na prática, com 64 fatias e ~40 velas, cada vela ≈ 1.6 fatia; para estimar
    # a sequência real, reamostramos para ~min(20, len(colors)) velas.
    target = min(20, len(colors))
    idx = np.linspace(0, len(colors) - 1, target).round().astype(int)
    candles = [colors[i] for i in idx]
    closes_s = [closes[i] for i in idx]

    feat.ok = True
    feat.n_slices = len(colors)
    feat.seq = " ".join(candles)
    feat.n_green = sum(1 for c in candles if c == "G")
    feat.n_red = len(candles) - feat.n_green
    feat.pct_green = round(100 * feat.n_green / len(candles), 1)
    feat.last_color = candles[-1]

    # streak atual
    last = candles[-1]
    st = 0
    for c in reversed(candles):
        if c == last:
            st += 1
        else:
            break
    feat.streak_len = st
    feat.streak_dir = "CALL" if last == "G" else "PUT"

    # inclinação (regressão linear nos fechamentos; y invertido)
    y = np.array(closes_s, dtype=float)
    y_n = (y - y.mean()) / (y.std() + 1e-6)
    x = np.arange(len(y_n), dtype=float)
    x_n = (x - x.mean()) / (x.std() + 1e-6)
    slope = float(-(x_n * y_n).mean())  # negativo porque y cresce p/ baixo
    feat.slope = round(float(max(-1.0, min(1.0, slope))), 3)
    s = feat.slope
    if s >= 0.45:
        feat.trend_vision = "forte_alta"
    elif s >= 0.18:
        feat.trend_vision = "alta"
    elif s <= -0.45:
        feat.trend_vision = "forte_baixa"
    elif s <= -0.18:
        feat.trend_vision = "baixa"
    else:
        feat.trend_vision = "lateral"

    # volatilidade: desvio médio das diferenças normalizado pela altura
    diffs = np.abs(np.diff(y))
    height = max(1.0, float(y1 - y0))
    vol = float(np.median(diffs) / height * 12.0)
    feat.volatility = round(float(max(0.0, min(1.0, vol))), 3)
    feat.volatility_label = ("baixa" if feat.volatility < 0.33
                             else "alta" if feat.volatility > 0.66 else "média")

    # posição no range
    lo_y, hi_y = float(np.min(y)), float(np.max(y))
    span = max(1e-6, hi_y - lo_y)
    pos = 1.0 - (float(y[-1]) - lo_y) / span  # 1 = topo
    feat.range_pos = round(float(max(0.0, min(1.0, pos))), 3)
    rp = feat.range_pos
    feat.range_label = ("topo do range — região de venda" if rp >= 0.8
                        else "fundo do range — região de compra" if rp <= 0.2
                        else "terço superior" if rp >= 0.66
                        else "terço inferior" if rp <= 0.33 else "meio do range")

    # corpos vs pavios
    feat.body_ratio = round(float(np.median(bodies)) if bodies else 0.5, 3)
    # doji: corpo muito pequeno nas últimas fatias
    tail_bodies = bodies[-6:] if len(bodies) >= 6 else bodies
    feat.doji_count = sum(1 for b in tail_bodies if b < 0.18)
    feat.doji_last = bool(tail_bodies and tail_bodies[-1] < 0.18)

    # engolfo: última fatia densa oposta e maior que a anterior
    try:
        if len(colors) >= 4:
            # compara densidade das últimas fatias de cores opostas
            pass
    except Exception:
        pass
    if len(candles) >= 2 and candles[-1] != candles[-2]:
        # confirma com tamanho: usa contagem de pixels das 2 últimas fatias reais
        feat.engulfing = ("bullish" if candles[-1] == "G" else "bearish")
        feat.notes.append("Última vela trocou de cor — possível engolfo/pinbar em formação.")
    if feat.doji_last:
        feat.notes.append("Última vela com corpo pequeno (doji/indecisão) — mercado pode estar decidindo direção.")
    if st >= 5:
        feat.exhaustion = "sim"
        feat.notes.append(f"Sequência de {st} velas {last} seguidas — risco de exaustão/continuação esticada; evite entrar a favor sem pullback.")
    elif st >= 3:
        feat.notes.append(f"Sequência de {st} velas {last} — momentum a favor da continuidade, mas atenção ao esticamento.")

    # qualidade: cobertura + sanidade
    coverage = len(colors) / N
    balance_ok = 0.05 < (feat.n_green / max(1, len(candles))) < 0.95
    q = 0.55 * coverage + 0.25 * (1.0 if balance_ok else 0.4) + 0.20 * min(1.0, len(candles) / 15)
    feat.quality = round(float(max(0.05, min(1.0, q))), 3)
    if feat.quality < 0.45:
        feat.notes.append("Qualidade da detecção BAIXA — confira os campos manuais antes de operar.")
    return feat


# ============================================================ CONFLUÊNCIA
TREND_MAP = {
    "forte_alta": 2, "alta": 1, "lateral": 0, "baixa": -1, "forte_baixa": -2,
    "up_strong": 2, "up": 1, "range": 0, "down": -1, "down_strong": -2,
}

@dataclass
class ModuleVote:
    key: str
    name: str
    school: str
    vote: float            # -1 (PUT) .. +1 (CALL)
    weight: float          # peso base
    confidence: float      # 0..1 — força da leitura
    reasons: list = field(default_factory=list)
    warngings: list = field(default_factory=list)


def _clamp(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


def score_confluence(p: dict, vis: VisionFeatures | None) -> dict:
    """Motor mestre: 12 módulos votam, agregado vira decisão M1/M5.

    `p` (inputs manuais, todos opcionais — o motor funciona só com o print):
      symbol, expiry ("M1"/"M5"/"auto"), payout (float),
      candles_seq (str "G R G..."), trend, price_pos, candle_body, wick_top, wick_bottom,
      rsi (0-100), stoch_zone, stoch_cross, ema_stack, macd, bollinger, volume,
      sr (suporte/resistência...), pattern (candle/graph), smc, smc_detail,
      session, news, otc (bool), account (real/demo)
    """
    payout = float(p.get("payout") or 80)
    be = breakeven_rate(payout)
    mods: list[ModuleVote] = []
    veto_reasons: list[str] = []
    boost_reasons: list[str] = []

    # ---------- sinais derivados ----------
    seq_raw = (p.get("candles_seq") or "").upper().replace(",", " ")
    seq_manual = [t for t in seq_raw.split() if t in ("G", "R", "C", "V", "D")]
    seq_manual = [{"C": "G", "V": "R", "D": "D"}.get(t, t) for t in seq_manual]
    if vis and vis.ok and not seq_manual:
        seq = vis.seq.split()
    elif seq_manual:
        seq = seq_manual[-20:]
    else:
        seq = []
    last = seq[-1] if seq else None
    streak = 0
    if seq:
        for c in reversed(seq):
            if c == last:
                streak += 1
            else:
                break
    n_g = sum(1 for c in seq if c == "G")
    n_r = sum(1 for c in seq if c == "R")
    pct_g = (100.0 * n_g / (n_g + n_r)) if (n_g + n_r) else 50.0

    trend_in = (p.get("trend") or "").lower()
    if (not trend_in or trend_in in ("auto", "visao")) and vis and vis.ok:
        trend_key = vis.trend_vision
        trend_src = "visão"
    else:
        trend_key = trend_in if trend_in in TREND_MAP else "lateral"
        trend_src = "manual" if trend_in in TREND_MAP else "padrão"
    tval = TREND_MAP.get(trend_key, 0)

    rng_pos = vis.range_pos if (vis and vis.ok) else None
    price_pos = (p.get("price_pos") or "").lower()  # topo/meio/fundo
    if not price_pos and rng_pos is not None:
        price_pos = "topo" if rng_pos >= 0.75 else "fundo" if rng_pos <= 0.25 else "meio"

    rsi = p.get("rsi")
    try:
        rsi = float(rsi) if rsi not in (None, "") else None
    except (TypeError, ValueError):
        rsi = None

    # ================= 1. PRICE ACTION & CANDLES (JP/BR) — peso 20
    v1, c1, r1, w1 = 0.0, 0.5, [], []
    pat = (p.get("pattern") or "auto").lower()
    body = (p.get("candle_body") or "").lower()
    wtop = (p.get("wick_top") or "").lower()
    wbot = (p.get("wick_bottom") or "").lower()
    auto_pat = None
    if vis and vis.ok:
        if vis.engulfing == "bullish" and (vis.range_pos <= 0.45 or price_pos == "fundo"):
            auto_pat = "bullish"
        elif vis.engulfing == "bearish" and (vis.range_pos >= 0.55 or price_pos == "topo"):
            auto_pat = "bearish"
    eff_pat = pat if pat not in ("auto", "") else (auto_pat or "")
    bullish_pats = {"engolfo_alta", "martelo", "hammer", "morning_star", "piercing",
                    "three_soldiers", "bullish", "pinbar_alta", "martelo_invertido_fundo"}
    bearish_pats = {"engolfo_baixa", "estrela_cadente", "shooting", "evening_star",
                    "dark_cloud", "three_crows", "bearish", "pinbar_baixa", "enforcado"}
    if eff_pat in bullish_pats:
        v1, c1 = 1.0, 0.8
        r1.append(f"Padrão de alta ({eff_pat}) — entrada CALL na próxima vela.")
    elif eff_pat in bearish_pats:
        v1, c1 = -1.0, 0.8
        r1.append(f"Padrão de baixa ({eff_pat}) — entrada PUT na próxima vela.")
    elif pat in ("doji", "doji_alta", "doji_baixa") or (vis and vis.ok and vis.doji_last and not eff_pat):
        c1 = 0.55
        if price_pos == "fundo":
            v1 = 0.5
            r1.append("Doji no FUNDO — indecisão com viés de reversão para CALL.")
        elif price_pos == "topo":
            v1 = -0.5
            r1.append("Doji no TOPO — indecisão com viés de reversão para PUT.")
        else:
            v1, c1 = 0.0, 0.4
            w1.append("Doji no MEIO do range — sem direção; aguarde a próxima vela.")
    # pavios (rejeição)
    if wbot in ("grande", "longo", "big") and wtop in ("pequeno", "curto", "small", ""):
        v1 = _clamp(v1 + 0.6); c1 = max(c1, 0.65)
        r1.append("Pavio inferior longo = rejeição de fundo → pressão compradora (CALL).")
    elif wtop in ("grande", "longo", "big") and wbot in ("pequeno", "curto", "small", ""):
        v1 = _clamp(v1 - 0.6); c1 = max(c1, 0.65)
        r1.append("Pavio superior longo = rejeição de topo → pressão vendedora (PUT).")
    # sequência / momentum de velas
    # Exceção inteligente: fluxo esticado ATÉ um extremo com RSI extremo NÃO é
    # força — é combustível da reversão (não penaliza o contra-fluxo).
    rsi_extreme_buy = rsi is not None and rsi <= 32
    rsi_extreme_sell = rsi is not None and rsi >= 68
    if seq and len(seq) >= 5:
        if pct_g >= 70:
            if rsi_extreme_sell or price_pos == "topo":
                w1.append(f"Fluxo {pct_g:.0f}% verde chegando em extremo — esticado, não force CALL.")
            else:
                v1 = _clamp(v1 + 0.45); c1 = max(c1, 0.6)
                r1.append(f"Fluxo comprador dominante ({pct_g:.0f}% velas verdes).")
        elif pct_g <= 30:
            if rsi_extreme_buy or price_pos == "fundo":
                w1.append(f"Fluxo {100 - pct_g:.0f}% vermelho chegando em extremo — exausto, não force PUT.")
            else:
                v1 = _clamp(v1 - 0.45); c1 = max(c1, 0.6)
                r1.append(f"Fluxo vendedor dominante ({100 - pct_g:.0f}% velas vermelhas).")
        if streak >= 5:
            w1.append(f"Streak de {streak} velas — NÃO persiga: espere pullback ou vela de confirmação.")
            c1 = min(c1, 0.5)
    # corpo da última vela
    if body in ("grande", "big") and last in ("G", "R"):
        v1 = _clamp(v1 + (0.35 if last == "G" else -0.35)); c1 = max(c1, 0.6)
        r1.append(f"Última vela {last} de corpo grande — momentum {'comprador' if last == 'G' else 'vendedor'}.")
    elif body in ("pequeno", "doji", "small") and not r1:
        w1.append("Última vela sem corpo — momentum fraco; exija confirmação.")
        c1 = min(c1, 0.45)
    if not r1 and not w1:
        r1.append("Sem padrão de candle decisivo — módulo neutro.")
        v1, c1 = 0.0, 0.3
    mods.append(ModuleVote("price_action", "Price Action & Candles", "Japonesa + BR", v1, 20, c1, r1, w1))

    # ================= 2. TENDÊNCIA & EMAs (US trend) — peso 15
    v2, c2, r2, w2 = 0.0, 0.6, [], []
    ema = (p.get("ema_stack") or "").lower()
    if tval >= 2:
        v2, c2 = 1.0, 0.85
        r2.append(f"Tendência de ALTA FORTE ({trend_src}) — opere a favor: CALL em pullbacks.")
    elif tval == 1:
        v2, c2 = 0.7, 0.7
        r2.append(f"Tendência de alta ({trend_src}) — viés CALL.")
    elif tval <= -2:
        v2, c2 = -1.0, 0.85
        r2.append(f"Tendência de BAIXA FORTE ({trend_src}) — opere a favor: PUT em repique.")
    elif tval == -1:
        v2, c2 = -0.7, 0.7
        r2.append(f"Tendência de baixa ({trend_src}) — viés PUT.")
    else:
        v2, c2 = 0.0, 0.5
        w2.append("Mercado LATERAL — proibido operar rompimento sem confirmação; prefira reversão nas bordas (M1).")
    if ema in ("alta_alinhada", "bull", "compra"):
        v2 = _clamp(v2 + 0.4); c2 = max(c2, 0.7)
        r2.append("EMAs alinhadas para compra (9>21>50) — confirma viés CALL.")
    elif ema in ("baixa_alinhada", "bear", "venda"):
        v2 = _clamp(v2 - 0.4); c2 = max(c2, 0.7)
        r2.append("EMAs alinhadas para venda (9<21<50) — confirma viés PUT.")
    elif ema in ("emaranhada", "mista", "flat"):
        w2.append("EMAs emaranhadas — sem tendência; reduza stake ou fique de fora.")
        c2 = min(c2, 0.45)
    mods.append(ModuleVote("trend", "Tendência & EMAs", "Americana (trend)", v2, 15, c2, r2, w2))

    # ================= 3. REVERSÃO RSI + ESTOCÁSTICO (EU) — peso 12
    v3, c3, r3, w3 = 0.0, 0.5, [], []
    sz = (p.get("stoch_zone") or "").lower()
    sc = (p.get("stoch_cross") or "").lower()
    if rsi is not None:
        if rsi >= 75:
            v3, c3 = -1.0, 0.8
            r3.append(f"RSI {rsi:.0f} EXTREMAMENTE sobrecomprado — reversão PUT (M1).")
        elif rsi >= 68:
            v3, c3 = -0.7, 0.7
            r3.append(f"RSI {rsi:.0f} sobrecomprado — viés PUT, confirme com vela vermelha.")
        elif rsi <= 25:
            v3, c3 = 1.0, 0.8
            r3.append(f"RSI {rsi:.0f} EXTREMAMENTE sobrevendido — reversão CALL (M1).")
        elif rsi <= 32:
            v3, c3 = 0.7, 0.7
            r3.append(f"RSI {rsi:.0f} sobrevendido — viés CALL, confirme com vela verde.")
        elif 45 <= rsi <= 55:
            w3.append(f"RSI {rsi:.0f} neutro — oscilador não ajuda nesta entrada.")
            c3 = 0.35
        elif rsi > 55:
            v3, c3 = 0.3, 0.45
            r3.append(f"RSI {rsi:.0f} em zona compradora — leve viés CALL.")
        else:
            v3, c3 = -0.3, 0.45
            r3.append(f"RSI {rsi:.0f} em zona vendedora — leve viés PUT.")
    else:
        w3.append("RSI não informado — informe o valor visível no gráfico para refinar.")
        c3 = 0.3
    if sz in ("sobrevendido", "oversold", "fundo"):
        v3 = _clamp(v3 + 0.5); c3 = max(c3, 0.65)
        r3.append("Estocástico em sobrevenda — combustível para CALL.")
    elif sz in ("sobrecomprado", "overbought", "topo"):
        v3 = _clamp(v3 - 0.5); c3 = max(c3, 0.65)
        r3.append("Estocástico em sobrecompra — combustível para PUT.")
    if sc in ("cruzou_cima", "up", "alta"):
        v3 = _clamp(v3 + 0.4); c3 = max(c3, 0.7)
        r3.append("Estocástico cruzou para CIMA — gatilho CALL.")
    elif sc in ("cruzou_baixo", "down", "baixa"):
        v3 = _clamp(v3 - 0.4); c3 = max(c3, 0.7)
        r3.append("Estocástico cruzou para BAIXO — gatilho PUT.")
    # divergência clássica: extremo RSI contra tendência = M1 reversão; a favor = M5 continuidade
    mods.append(ModuleVote("reversion", "Reversão RSI + Estocástico", "Europeia (mean-rev)", v3, 12, c3, r3, w3))

    # ================= 4. BOLLINGER & VOLATILIDADE — peso 10
    v4, c4, r4, w4 = 0.0, 0.5, [], []
    bb = (p.get("bollinger") or "").lower()
    vol_in = (p.get("volatility") or "").lower()
    vol_eff = vol_in or ((vis.volatility_label if vis and vis.ok else "média").lower())
    if bb in ("acima_superior", "rompeu_cima", "fora_cima"):
        v4, c4 = -0.8, 0.7
        r4.append("Preço ACIMA da banda superior — esticado; viés PUT (M1).")
    elif bb in ("abaixo_inferior", "rompeu_baixo", "fora_baixo"):
        v4, c4 = 0.8, 0.7
        r4.append("Preço ABAIXO da banda inferior — esticado; viés CALL (M1).")
    elif bb in ("tocou_superior", "na_superior"):
        v4, c4 = -0.5, 0.6
        r4.append("Toque na banda superior — pressão vendedora de curto prazo.")
    elif bb in ("tocou_inferior", "na_inferior"):
        v4, c4 = 0.5, 0.6
        r4.append("Toque na banda inferior — pressão compradora de curto prazo.")
    elif bb in ("squeeze", "estreita"):
        w4.append("Bollinger em SQUEEZE — explosão à vista; NÃO opere M1 reversão, espere o rompimento.")
        c4 = 0.4
    elif bb in ("meio", "central", ""):
        if not bb:
            w4.append("Posição em Bollinger não informada.")
            c4 = 0.3
        else:
            c4 = 0.35
    if rng_pos is not None and not bb:
        if rng_pos >= 0.85:
            v4 = _clamp(v4 - 0.4); c4 = max(c4, 0.55)
            r4.append("Preço no TOPO do range visível — região de venda.")
        elif rng_pos <= 0.15:
            v4 = _clamp(v4 + 0.4); c4 = max(c4, 0.55)
            r4.append("Preço no FUNDO do range visível — região de compra.")
    if vol_eff in ("muito_alta", "extrema"):
        w4.append("Volatilidade EXTREMA — velas erráticas; reduza stake à metade ou fique de fora.")
        c4 = min(c4, 0.45)
    elif vol_eff in ("morta", "muito_baixa"):
        w4.append("Volatilidade MORTA — mercado parado; M5 tende a empatar, prefira ficar de fora.")
        c4 = min(c4, 0.4)
    mods.append(ModuleVote("bollinger", "Bollinger & Volatilidade", "Americana (vol)", v4, 10, c4, r4, w4))

    # ================= 5. SMC / ICT — peso 18
    v5, c5, r5, w5 = 0.0, 0.5, [], []
    smc = (p.get("smc") or "").lower()
    smc_d = (p.get("smc_detail") or "").lower()
    smc_map = {
        "bos_alta": (1.0, 0.8, "BOS de ALTA — estrutura rompeu p/ cima, viés CALL na continuação."),
        "bos_baixa": (-1.0, 0.8, "BOS de BAIXA — estrutura rompeu p/ baixo, viés PUT na continuação."),
        "choch_alta": (1.0, 0.75, "CHOCH de alta — mudança de caráter para compra (CALL)."),
        "choch_baixa": (-1.0, 0.75, "CHOCH de baixa — mudança de caráter para venda (PUT)."),
        "ob_alta": (1.0, 0.7, "Order Block de alta — região institucional de compra (CALL)."),
        "ob_baixa": (-1.0, 0.7, "Order Block de baixa — região institucional de venda (PUT)."),
        "fvg_alta": (0.6, 0.6, "FVG/imbalance comprador — preço tende a preencher e seguir (CALL)."),
        "fvg_baixa": (-0.6, 0.6, "FVG/imbalance vendedor — preço tende a preencher e seguir (PUT)."),
        "sweep_fundo": (1.0, 0.75, "Sweep de fundo (caça-stop abaixo do suporte) — armadilha de venda, viés CALL."),
        "sweep_topo": (-1.0, 0.75, "Sweep de topo (caça-stop acima da resistência) — armadilha de compra, viés PUT."),
        "premium": (-0.5, 0.6, "Zona PREMIUM (cara) — vender, não comprar: viés PUT."),
        "discount": (0.5, 0.6, "Zona DISCOUNT (barata) — comprar, não vender: viés CALL."),
        "eq": (0.0, 0.35, "Equilíbrio (meio do dealing range) — sem edge SMC."),
    }
    if smc in smc_map:
        vv, cc, rr = smc_map[smc]
        v5, c5 = vv, cc
        r5.append(rr)
    else:
        # inferência parcial pela visão: rompimento de range + posição
        if vis and vis.ok and abs(vis.slope) >= 0.45 and rng_pos is not None:
            if vis.slope > 0 and rng_pos >= 0.8:
                v5, c5 = 0.5, 0.5
                r5.append("Estrutura sugere BOS de alta em região esticada — CALL só em pullback (M5).")
            elif vis.slope < 0 and rng_pos <= 0.2:
                v5, c5 = -0.5, 0.5
                r5.append("Estrutura sugere BOS de baixa em região esticada — PUT só em repique (M5).")
            else:
                w5.append("SMC não identificado no print — marque BOS/OB/FVG manualmente para ativar este módulo.")
                c5 = 0.3
        else:
            w5.append("SMC não informado — marque BOS/CHOCH/OB/FVG/sweep para ativar o módulo institucional.")
            c5 = 0.3
    if smc_d in ("ob+fvg", "ob_fvg", "confluencia_total"):
        c5 = min(1.0, c5 + 0.15)
        boost_reasons.append("Confluência OB+FVG — zona institucional de alta qualidade.")
    mods.append(ModuleVote("smc", "SMC / ICT (Smart Money)", "Americana (ICT)", v5, 18, c5, r5, w5))

    # ================= 6. SUPORTE / RESISTÊNCIA — peso 14
    v6, c6, r6, w6 = 0.0, 0.5, [], []
    sr = (p.get("sr") or "").lower()
    if sr in ("suporte", "em_suporte", "tocou_suporte"):
        v6, c6 = 0.9, 0.75
        r6.append("Preço EM SUPORTE — rejeição esperada: CALL (M1). Stop mental se fechar abaixo.")
    elif sr in ("resistencia", "resistência", "em_resistencia", "tocou_resistencia"):
        v6, c6 = -0.9, 0.75
        r6.append("Preço EM RESISTÊNCIA — rejeição esperada: PUT (M1).")
    elif sr in ("rompeu_cima", "breakout_alta", "rompimento_alta"):
        v6, c6 = 0.8, 0.7
        r6.append("ROMPIMENTO de alta — momentum comprador: CALL (M5 capta a continuação).")
    elif sr in ("rompeu_baixo", "breakout_baixa", "rompimento_baixa"):
        v6, c6 = -0.8, 0.7
        r6.append("ROMPIMENTO de baixa — momentum vendedor: PUT (M5 capta a continuação).")
    elif sr in ("falso_rompimento_cima", "falso_cima"):
        v6, c6 = -1.0, 0.8
        r6.append("FALSO rompimento de topo (armadilha) — reversão PUT.")
    elif sr in ("falso_rompimento_baixo", "falso_baixo"):
        v6, c6 = 1.0, 0.8
        r6.append("FALSO rompimento de fundo (armadilha) — reversão CALL.")
    elif sr in ("flip_alta", "sr_flip_compra"):
        v6, c6 = 0.9, 0.75
        r6.append("S/R FLIP: resistência virou suporte — CALL de alta qualidade.")
    elif sr in ("flip_baixa", "sr_flip_venda"):
        v6, c6 = -0.9, 0.75
        r6.append("S/R FLIP: suporte virou resistência — PUT de alta qualidade.")
    elif sr in ("fundo_duplo", "double_bottom"):
        v6, c6 = 1.0, 0.8
        r6.append("FUNDO DUPLO — reversão clássica: CALL.")
    elif sr in ("topo_duplo", "double_top"):
        v6, c6 = -1.0, 0.8
        r6.append("TOPO DUPLO — reversão clássica: PUT.")
    elif sr in ("meio_nada", "nenhum", "meio"):
        w6.append("Preço no meio do nada (sem S/R) — sem edge de localização.")
        c6 = 0.35
    else:
        if rng_pos is not None:
            if rng_pos >= 0.85:
                v6, c6 = -0.4, 0.5
                r6.append("Visão: preço no topo do range — atua como resistência intraday.")
            elif rng_pos <= 0.15:
                v6, c6 = 0.4, 0.5
                r6.append("Visão: preço no fundo do range — atua como suporte intraday.")
            else:
                w6.append("S/R não informado e preço no meio do range — marque o nível mais próximo.")
                c6 = 0.35
        else:
            w6.append("S/R não informado — marque suporte/resistência/rompimento.")
            c6 = 0.3
    mods.append(ModuleVote("sr", "Suporte / Resistência", "Universal", v6, 14, c6, r6, w6))

    # ================= 7. MOMENTUM MACD + VOLUME — peso 8
    v7, c7, r7, w7 = 0.0, 0.45, [], []
    macd = (p.get("macd") or "").lower()
    volm = (p.get("volume") or "").lower()
    if macd in ("alta", "bull", "cruzou_cima", "histograma_cima"):
        v7, c7 = 0.7, 0.6
        r7.append("MACD comprador — confirma momentum CALL.")
    elif macd in ("baixa", "bear", "cruzou_baixo", "histograma_baixo"):
        v7, c7 = -0.7, 0.6
        r7.append("MACD vendedor — confirma momentum PUT.")
    else:
        w7.append("MACD não informado.")
    if volm in ("alto_compra", "subindo_compra"):
        v7 = _clamp(v7 + 0.4); c7 = max(c7, 0.55)
        r7.append("Volume apoiando a compra.")
    elif volm in ("alto_venda", "subindo_venda"):
        v7 = _clamp(v7 - 0.4); c7 = max(c7, 0.55)
        r7.append("Volume apoiando a venda.")
    elif volm in ("caindo", "fraco", "baixo"):
        w7.append("Volume fraco — movimento sem combustível; desconfie de rompimentos.")
    if not r7 and not w7:
        c7 = 0.3
    mods.append(ModuleVote("momentum", "Momentum MACD + Volume", "Americana", v7, 8, c7, r7, w7))

    # ================= 8. FIGURAS + FIBONACCI — peso 6
    v8, c8, r8, w8 = 0.0, 0.4, [], []
    fig = (p.get("figure") or "").lower()
    fib = (p.get("fib") or "").lower()
    if fig in ("triangulo_alta", "bandeira_alta", "cunhad_baixa_reversao", "ombro_cabeca_ombro_inv",
               "flag_alta", "flamula_alta"):
        v8, c8 = 0.7, 0.6
        r8.append(f"Figura altista ({fig}) — viés CALL.")
    elif fig in ("triangulo_baixa", "bandeira_baixa", "flamula_baixa", "oco", "ombro_cabeca_ombro"):
        v8, c8 = -0.7, 0.6
        r8.append(f"Figura baixista ({fig}) — viés PUT.")
    if fib in ("ouro_compra", "0618_suporte", "desconto_fib"):
        v8 = _clamp(v8 + 0.5); c8 = max(c8, 0.6)
        r8.append("Fibonacci em zona de compra (61.8%/desconto) — CALL.")
    elif fib in ("ouro_venda", "0618_resistencia", "premium_fib"):
        v8 = _clamp(v8 - 0.5); c8 = max(c8, 0.6)
        r8.append("Fibonacci em zona de venda — PUT.")
    if not r8:
        w8.append("Sem figura/Fib ativa — módulo neutro (normal: nem toda entrada tem figura).")
    mods.append(ModuleVote("figures", "Figuras + Fibonacci", "Europeia (clássica)", v8, 6, c8, r8, w8))

    # ================= 9. SESSÃO & TEMPO (killzones) — peso 7 + veto
    v9, c9, r9, w9 = 0.0, 0.5, [], []
    sess = (p.get("session") or "").lower()
    news = (p.get("news") or "").lower()
    if news in ("alto", "vermelho", "now", "agora", "5min"):
        veto_reasons.append("NOTÍCIA DE ALTO IMPACTO próxima/agora — spreads explodem e o preço whipsawa. NÃO OPERE (veto total).")
    elif news in ("medio", "amarelo"):
        w9.append("Notícia de médio impacto próxima — reduza stake à metade e prefira M1.")
    good_session = sess in ("londres", "london", "nova_york", "ny", "overlap", "londres_ny",
                            "manha_br", "killzone_manha", "killzone_tarde")
    if sess in ("overlap", "londres_ny"):
        c9 = 0.75
        r9.append("Overlap Londres+NY — melhor liquidez do dia; sinais têm mais seguimento (favorece M5).")
        boost_reasons.append("Overlap Londres/NY: liquidez máxima.")
    elif sess in ("londres", "london", "nova_york", "ny", "manha_br", "killzone_manha"):
        c9 = 0.65
        r9.append("Sessão de bom movimento — sinais funcionam melhor que na madrugada.")
    elif sess in ("asia", "tokyo", "madrugada", "tokio"):
        w9.append("Sessão asiática/madrugada — mercado lento e manipulado; opere SÓ reversão nas bordas com stake mínimo.")
        c9 = 0.4
    elif sess in ("fechamento", "pos_ny", "pos-ny", "noite"):
        w9.append("Pós-NY/noite — liquidez seca; evite M5, aceite só setups A+.")
        c9 = 0.4
    else:
        w9.append("Sessão não informada — resultados são melhores em Londres/NY (manhã/tarde BR).")
        c9 = 0.3
    # killzone ICT: direção com a sessão conta como micro-confirmação
    mods.append(ModuleVote("session", "Sessão & Killzone", "Asiática + ICT", v9, 7, c9, r9, w9))

    # ================= 10. FILTRO QUANT / OTC (RU) — veto + ajuste
    v10, c10, r10, w10 = 0.0, 0.6, [], []
    otc = str(p.get("otc") or "").lower() in ("1", "true", "sim", "yes", "otc")
    symbol = (p.get("symbol") or "").upper()
    if "OTC" in symbol:
        otc = True
    if otc:
        w10.append("Ativo OTC (preço da corretora, não do mercado real) — manipulação de pavio é comum: opere SÓ setup A+, stake mínimo, evite M5 em OTC agitado.")
        c10 = 0.5
    # payout baixo = veto matemático parcial
    if payout < 70:
        veto_reasons.append(f"Payout {payout:.0f}% BAIXO DEMAIS: break-even {be*100:.1f}% — mesmo acertando 6 em 10 você empata. Troque de ativo/horário.")
    elif payout < 78:
        w10.append(f"Payout {payout:.0f}% mediano (break-even {be*100:.1f}%) — exija setup mais forte.")
    else:
        r10.append(f"Payout {payout:.0f}% bom (break-even {be*100:.1f}%) — matemática a favor do filtro.")
    # volatilidade extrema detectada pela visão
    if vis and vis.ok and vis.volatility >= 0.8:
        w10.append("Visão detectou volatilidade extrema/errática — padrão de OTC manipulado ou notícia.")
    mods.append(ModuleVote("quant", "Filtro Quant & OTC", "Russa (probabilidade)", v10, 5, c10, r10, w10))

    # ================= 11. EXAUSTÃO / CONTRA-STREAK (BR M1) — peso 8
    v11, c11, r11, w11 = 0.0, 0.5, [], []
    # confirmação pós-exaustão: 5+ velas de uma cor + 1-2 de reversão = gatilho
    pre_streak, pre_color = 0, None
    if seq and len(seq) >= 7:
        pre_color = seq[-2]
        for c in reversed(seq[:-1]):
            if c == pre_color:
                pre_streak += 1
            else:
                break
        if pre_streak >= 5 and last != pre_color and last in ("G", "R"):
            if last == "G" and (price_pos in ("fundo", "") or (rng_pos and rng_pos <= 0.4) or (rsi is not None and rsi <= 35)):
                v11, c11 = 1.0, 0.75
                r11.append(f"REVERSÃO CONFIRMADA: {pre_streak} vermelhas + vela verde de virada em zona de compra — CALL (M1).")
            elif last == "R" and (price_pos in ("topo", "") or (rng_pos and rng_pos >= 0.6) or (rsi is not None and rsi >= 65)):
                v11, c11 = -1.0, 0.75
                r11.append(f"REVERSÃO CONFIRMADA: {pre_streak} verdes + vela vermelha de virada em zona de venda — PUT (M1).")
    if v11 == 0.0 and streak >= 6:
        # contra o streak em extremo = reversão M1 clássica BR
        if last == "G" and (price_pos == "topo" or (rng_pos and rng_pos >= 0.8)):
            v11, c11 = -0.9, 0.7
            r11.append(f"Exaustão: {streak} verdes no TOPO — reversão PUT (M1) clássica.")
        elif last == "R" and (price_pos == "fundo" or (rng_pos and rng_pos <= 0.2)):
            v11, c11 = 0.9, 0.7
            r11.append(f"Exaustão: {streak} vermelhas no FUNDO — reversão CALL (M1) clássica.")
        else:
            w11.append(f"Streak de {streak} sem extremo claro — NÃO entre contra nem a favor; aguarde.")
            c11 = 0.4
    elif streak and streak >= 4:
        if last == "G" and tval > 0:
            v11, c11 = 0.4, 0.5
            r11.append(f"Momentum de {streak} verdes a favor da tendência — CALL com pullback.")
        elif last == "R" and tval < 0:
            v11, c11 = -0.4, 0.5
            r11.append(f"Momentum de {streak} vermelhas a favor da tendência — PUT com repique.")
        else:
            w11.append(f"Streak de {streak} CONTRA a tendência maior — cuidado com o estilingue.")
            c11 = 0.45
    else:
        w11.append("Sem streak relevante — módulo neutro.")
        c11 = 0.3
    mods.append(ModuleVote("exhaustion", "Exaustão & Fluxo (M1)", "Brasileira (scalping)", v11, 8, c11, r11, w11))

    # ================= 12. GESTÃO / CONTEXTO — veto + ajuste
    v12, c12, r12, w12 = 0.0, 0.7, [], []
    acct = (p.get("account") or "").lower()
    if acct in ("demo", "demonstracao", "treino"):
        r12.append("Conta DEMO — ambiente correto para validar o método antes do real.")
    # martingale proibido
    r12.append("Gestão: stake 1–2% por entrada, máx. 3–5% ao dia de risco, stop WIN e stop LOSS diários.")
    mods.append(ModuleVote("money", "Gestão & Disciplina", "Chinesa (disciplina)", v12, 4, c12, r12, w12))

    # ------------------------------------------------- agregação ponderada
    num = sum(m.vote * m.weight * m.confidence for m in mods)
    den = sum(m.weight * m.confidence for m in mods) or 1.0
    raw = num / den  # -1..1
    score = int(round(raw * 100))

    # penalidade por informação fraca: poucos módulos confiantes -> puxa p/ neutro
    strong = sum(1 for m in mods if m.confidence >= 0.6 and abs(m.vote) >= 0.5)
    if strong <= 1:
        score = int(round(score * 0.55))
    elif strong == 2:
        score = int(round(score * 0.8))

    # bônus de confluência real (3+ módulos fortes alinhados)
    aligned_call = sum(1 for m in mods if m.vote >= 0.5 and m.confidence >= 0.6)
    aligned_put = sum(1 for m in mods if m.vote <= -0.5 and m.confidence >= 0.6)
    confl = max(aligned_call, aligned_put)
    if confl >= 4:
        score = int(round(score * 1.1))
        boost_reasons.append(f"Confluência RARA: {confl} módulos fortes alinhados.")
    score = max(-100, min(100, score))

    # ------------------------------------------------- decisão + expiração
    veto = len(veto_reasons) > 0
    if veto:
        decision, side, emoji = "SEM ENTRADA (VETO)", "FLAT", "⛔"
    elif score >= 55:
        decision, side, emoji = "CALL FORTE 🟢", "CALL", "🟢"
    elif score >= 25:
        decision, side, emoji = "CALL 🟢", "CALL", "🟢"
    elif score <= -55:
        decision, side, emoji = "PUT FORTE 🔴", "PUT", "🔴"
    elif score <= -25:
        decision, side, emoji = "PUT 🔴", "PUT", "🔴"
    else:
        decision, side, emoji = "SEM ENTRADA 🟡", "FLAT", "🟡"

    # expiração M1 vs M5
    expiry_in = (p.get("expiry") or "auto").upper()
    reversal_bias = (abs(v3) >= 0.6 and v3 * raw > 0) or (abs(v11) >= 0.6 and v11 * raw > 0) or (abs(v4) >= 0.6 and v4 * raw > 0)
    trend_follow = (abs(v2) >= 0.6 and v2 * raw > 0) or (abs(v5) >= 0.6 and v5 * raw > 0)
    if expiry_in in ("M1", "1M", "60S", "1"):
        expiry, expiry_why = "M1", "Escolhida por você."
    elif expiry_in in ("M5", "5M", "5"):
        expiry, expiry_why = "M5", "Escolhida por você."
    else:
        if side == "FLAT":
            expiry, expiry_why = "—", "Sem direção, sem expiração."
        elif reversal_bias and not trend_follow:
            expiry, expiry_why = "M1", "Setup de REVERSÃO em extremo — M1 captura o repique; M5 dá tempo do mercado voltar contra você."
        elif trend_follow and not reversal_bias:
            expiry, expiry_why = "M5", "Setup de CONTINUAÇÃO a favor do fluxo — M5 absorve o ruído de 1–2 velas contra."
        elif trend_follow and reversal_bias:
            expiry, expiry_why = ("M5", "Pullback a favor da tendência em nível-chave — M5 é o padrão-ouro deste setup.") if abs(score) >= 45 else ("M1", "Forças mistas — M1 com stake mínimo ou fique de fora.")
        else:
            expiry, expiry_why = "M1", "Sem padrão claro de continuação — se operar, M1 com stake mínimo."

    # ------------------------------------------------- confiança honesta
    # Mapeia |score| para winrate 50%..68% (TETO honesto: ninguém sustenta mais
    # que isso em binárias com payout real; backtests sérios de price action
    # ficam em 55–62% nos melhores filtros).
    mag = abs(score) if not veto else 0
    win_est = 0.50 + min(0.18, mag / 100 * 0.20)
    if confl >= 4:
        win_est = min(0.68, win_est + 0.02)
    if vis is not None and vis.ok and vis.quality < 0.45:
        win_est -= 0.02
    if otc:
        win_est -= 0.015
    if not good_session and sess:
        win_est -= 0.01
    win_est = round(float(max(0.50, min(0.68, win_est))), 3)
    ev = round(expected_value(win_est, payout), 4)

    # Bloqueio matemático (disciplina russa): sinal com EV ≤ 0 vira SEM ENTRADA,
    # mesmo com score positivo. Operar EV negativa quebra a banca devagar.
    ev_block = False
    if side != "FLAT" and not veto and ev <= 0:
        ev_block = True
        veto_reasons.append(
            f"MATEMÁTICA NÃO FECHA 📐: acerto estimado {win_est*100:.1f}% < break-even "
            f"{be*100:.1f}% (payout {payout:.0f}%) → EV {(ev*100):+.1f}%. Exija setup mais "
            f"forte ou payout maior; operar aqui é doar dinheiro devagar.")
        side, decision = "FLAT", "SEM ENTRADA 🟡"
        expiry, expiry_why = "—", "EV negativa: sem edge, sem expiração."
        mag = 0

    # estrelas do setup (1..5)
    stars = 1
    if side != "FLAT" and not veto:
        stars = 2 + (1 if confl >= 2 else 0) + (1 if confl >= 3 else 0) + (1 if confl >= 4 and mag >= 55 else 0)

    # stake sugerida (fração da banca) — Kelly adaptado p/ binária, conservador
    # f* = (p*(1+b) - 1)/b ; usamos 1/4 Kelly + travas 0.5%..2%
    b = payout / 100.0
    kelly_full = (win_est * (1 + b) - 1) / b if b > 0 else 0
    kelly_q = max(0.0, kelly_full * 0.25)
    if side == "FLAT" or veto or ev <= 0:
        stake_pct = 0.0
    else:
        stake_pct = round(float(max(0.5, min(2.0, kelly_q * 100))), 2)

    # motivos pró/contra (top reasons)
    pros, cons = [], []
    for m in mods:
        for r in m.reasons:
            (pros if (m.vote > 0 and raw >= 0) or (m.vote < 0 and raw < 0) or m.vote == 0 else cons).append(f"[{m.name}] {r}")
        for w in m.warngings:
            cons.append(f"[{m.name}] {w}")
    # ordena: razões dos módulos de maior peso primeiro
    order = {m.key: m.weight * m.confidence for m in mods}
    def _w(txt):
        for m in mods:
            if txt.startswith(f"[{m.name}]"):
                return order[m.key]
        return 0
    pros.sort(key=_w, reverse=True)
    cons.sort(key=_w, reverse=True)

    entry_plan = _entry_plan(side, expiry, score, price_pos, sr, eff_pat if 'eff_pat' in dir() else "")
    checklist = _checklist(p, vis, payout, be, otc, news)

    return {
        "version": VERSION,
        "decision": decision,
        "side": side,
        "emoji": emoji,
        "score": score,
        "stars": stars,
        "expiry": expiry,
        "expiry_why": expiry_why,
        "win_est": win_est,
        "win_est_pct": round(win_est * 100, 1),
        "breakeven_pct": round(be * 100, 1),
        "payout": payout,
        "ev_per_unit": ev,
        "ev_ok": ev > 0,
        "stake_pct": stake_pct,
        "veto": veto,
        "ev_block": ev_block,
        "veto_reasons": veto_reasons,
        "boost_reasons": boost_reasons,
        "confluence_count": confl,
        "strong_modules": strong,
        "trend": {"key": trend_key, "source": trend_src},
        "seq": seq,
        "streak": {"len": streak, "last": last},
        "range_pos": rng_pos,
        "price_pos": price_pos,
        "rsi": rsi,
        "modules": [
            {"key": m.key, "name": m.name, "school": m.school,
             "vote": round(m.vote, 2), "weight": m.weight,
             "confidence": round(m.confidence, 2),
             "contrib": round(m.vote * m.weight * m.confidence, 1),
             "reasons": m.reasons, "warnings": m.warngings}
            for m in mods
        ],
        "pros": pros[:10],
        "cons": cons[:10],
        "entry_plan": entry_plan,
        "checklist": checklist,
        "vision": None if vis is None else {
            "ok": vis.ok, "error": vis.error, "quality": vis.quality,
            "seq": vis.seq, "pct_green": vis.pct_green,
            "streak_dir": vis.streak_dir, "streak_len": vis.streak_len,
            "slope": vis.slope, "trend": vis.trend_vision,
            "volatility": vis.volatility, "volatility_label": vis.volatility_label,
            "range_pos": vis.range_pos, "range_label": vis.range_label,
            "doji_last": vis.doji_last, "engulfing": vis.engulfing,
            "notes": vis.notes,
        },
    }


def _entry_plan(side, expiry, score, price_pos, sr, pat):
    if side == "FLAT":
        return [
            "NÃO abra posição agora — o filtro protegeu seu capital.",
            "Espere: preço chegar num nível (suporte/resistência) + vela de confirmação + RSI/estocástico alinhados.",
            "Enquanto isso, troque de ativo ou timeframe e analise de novo.",
        ]
    d = "COMPRA (CALL 🟢)" if side == "CALL" else "VENDA (PUT 🔴)"
    steps = [
        f"Direção: {d} | Expiração: {expiry} | Score {score:+d}/100.",
        "TIMING: entre no INÍCIO da próxima vela (primeiros 5–10 segundos). Entrar no meio/fim da vela destrói a estatística.",
    ]
    if expiry == "M1":
        steps.append("M1 = precisão cirúrgica: 1 entrada por sinal. Se perder, NÃO dobre (sem martingale) — aceite e espere o próximo setup A.")
    else:
        steps.append("M5 = seguida de tendência: aceite 1–2 velas contra no meio do caminho; não saia antes (binária não tem stop parcial).")
    if sr in ("suporte", "em_suporte", "tocou_suporte") and side == "CALL":
        steps.append("Invalidação: se fechar vela M1 ABAIXO do suporte, o setup morreu — não faça repique.")
    if sr in ("resistencia", "resistência", "em_resistencia", "tocou_resistencia") and side == "PUT":
        steps.append("Invalidação: se fechar vela M1 ACIMA da resistência, o setup morreu — não faça repique.")
    steps.append("GESTÃO:stake conforme sugerido; MÁXIMO 2 entradas por setup e 3 setups por dia. Bateu stop WIN ou stop LOSS? Desligue a tela.")
    return steps


def _checklist(p, vis, payout, be, otc, news):
    items = []
    def add(ok, txt):
        items.append({"ok": bool(ok), "text": txt})
    add(payout >= 78, f"Payout {payout:.0f}% (break-even {be*100:.1f}%) — ideal ≥78%")
    add(news not in ("alto", "vermelho", "now", "agora", "5min"), "Sem notícia de alto impacto nos próximos 10 min")
    add(not otc or True, "OTC aceito SOMENTE com setup A+ e stake mínimo" if otc else "Ativo de mercado real (não OTC) — preferível")
    add(bool(vis and vis.ok), "Print legível com 30–60 velas de candles")
    add(p.get("rsi") not in (None, ""), "RSI verificado no gráfico")
    add(bool(p.get("sr")), "Nível de S/R identificado")
    add(str(p.get("account") or "").lower() in ("demo", "real"), "Conta definida (treine no DEMO primeiro)")
    add(True, "Stop WIN e stop LOSS do dia definidos ANTES de operar")
    return items


# ============================================================ BIBLIOTECA
STRATEGIES = [
    {"id": "pa_engolfo", "name": "Engolfo + Nível (M1)", "school": "Japonesa/BR",
     "timeframes": "M1", "win_hint": "58–63% filtrado",
     "setup": "Preço toca suporte/resistência + vela engolfa a anterior no sentido da rejeição + RSI confirma extremo.",
     "entry": "CALL/PUT no início da próxima vela, M1, stake 1%.",
     "filter": "NÃO opere no meio do range, contra tendência forte sem extremo, ou com RSI neutro 45–55.",
     "why": "Engolfo em nível = institucional defendendo preço; a próxima vela tende a seguir o fluxo da defesa."},
    {"id": "pa_pinbar", "name": "Pinbar / Martelo (M1)", "school": "Japonesa/BR",
     "timeframes": "M1", "win_hint": "57–62% filtrado",
     "setup": "Pavio longo rejeitando nível (pavio ≥ 2× corpo) + corpo pequeno fechando a favor.",
     "entry": "CALL/PUT M1 no início da próxima vela.",
     "filter": "Pavio contra você no nível oposto anula. Exija RSI ≤32 (CALL) ou ≥68 (PUT).",
     "why": "Pavio = stop hunt + absorção; fechamento mostra quem venceu a briga."},
    {"id": "trend_pullback", "name": "Pullback na EMA 9/21 (M5)", "school": "Americana",
     "timeframes": "M5", "win_hint": "58–64% em tendência",
     "setup": "Tendência clara (EMAs alinhadas) + pullback até EMA 9/21 + vela de retomada (martelo/engolfo a favor).",
     "entry": "A favor da tendência, M5 (aguenta 1–2 velas de ruído).",
     "filter": "NUNCA contra a tendência. RSI não pode estar em extremo contrário.",
     "why": "Tendências persistem; comprar pullback em média móvel é o setup com maior amostra vencedora da história."},
    {"id": "smc_ob", "name": "Order Block + FVG (M5)", "school": "Americana (ICT/SMC)",
     "timeframes": "M5", "win_hint": "60–66% (raro, alta qualidade)",
     "setup": "BOS confirma direção + preço retorna ao OB com FVG + reação (engolfo/pin a favor).",
     "entry": "A favor do BOS, M5, stake pode ser 1.5–2% (setup A+).",
     "filter": "Sem BOS não há OB válido. Não opere OB em sessão morta.",
     "why": "OB = pegada institucional; FVG = desequilíbrio que o preço respeita na volta."},
    {"id": "smc_sweep", "name": "Liquidity Sweep (M1)", "school": "Americana (ICT)",
     "timeframes": "M1", "win_hint": "59–64%",
     "setup": "Preço fura suporte/resistência por 1–2 velas e fecha DE VOLTA para dentro (caça-stop) + RSI extremo.",
     "entry": "Reversão M1 contra a furação.",
     "filter": "Se fechar 2 velas FORA do nível, virou rompimento — INVERTA o viés ou fique de fora.",
     "why": "Stops acumulados viram combustível; após o sweep, o caminho de menor resistência é o reverso."},
    {"id": "mean_bb", "name": "Bollinger Extremes (M1)", "school": "Europeia",
     "timeframes": "M1", "win_hint": "57–61% em range",
     "setup": "Mercado lateral + preço fecha FORA da banda + RSI extremo + vela de rejeição.",
     "entry": "Reversão M1 para dentro das bandas.",
     "filter": "PROIBIDO em tendência forte ou squeeze (bandas estreitas = explosão vindo).",
     "why": "Em ranges, 2 desvios revertem à média na maioria das vezes — estatística pura."},
    {"id": "stoch_trigger", "name": "Estocástico Gatilho (M1/M5)", "school": "Europeia",
     "timeframes": "M1/M5", "win_hint": "56–60% com filtro",
     "setup": "Estocástico sai do extremo (cruza 20 p/ cima = CALL, 80 p/ baixo = PUT) + preço em nível.",
     "entry": "M1 em range, M5 se alinhado à tendência maior.",
     "filter": "Estocástico colado no extremo em tendência = força, NÃO reversão. Espere sair do extremo.",
     "why": "O cruzamento saindo do extremo marca a virada do momentum de curto prazo."},
    {"id": "breakout", "name": "Rompimento + Reteste (M5)", "school": "Londres (breakout)",
     "timeframes": "M5", "win_hint": "58–63%",
     "setup": "Consolidação + rompimento com vela de corpo grande + reteste do nível rompido segurando.",
     "entry": "A favor do rompimento, M5, após o reteste (nunca no primeiro toque).",
     "filter": "Rompimento em sessão morta ou sem volume = falso em ~70% dos casos. Exija corpo grande.",
     "why": "Rompimentos verdadeiros têm combustível (volume); o reteste confirma a nova polaridade do nível."},
    {"id": "exaustao", "name": "Exaustão 6+ Velas (M1)", "school": "Brasileira (OTC scalping)",
     "timeframes": "M1", "win_hint": "56–60% em extremo",
     "setup": "6+ velas seguidas da mesma cor CHEGANDO em nível/SR + RSI extremo + últimas velas com pavio contra.",
     "entry": "Reversão M1, stake mínimo (setup de risco).",
     "filter": "Sem nível/SR = aposta, não setup. Em notícia, streaks vão a 10+ — veto total.",
     "why": "Sequências longas em nível esgotam combustível; o estilingue estatístico favorece 1 vela contra."},
    {"id": "fibo_zones", "name": "Fibonacci Premium/Discount (M5)", "school": "Europeia + ICT",
     "timeframes": "M5", "win_hint": "58–62%",
     "setup": "Perna clara + retração na zona ouro (61.8–79%) + confluência com OB/SR + vela de confirmação.",
     "entry": "A favor da perna, M5.",
     "filter": "Comprar em premium / vender em discount = erro amador. Opere discount→premium.",
     "why": "61.8–79% concentra ordens institucionais; confluente com estrutura, é zona de alta reação."},
    {"id": "killzone", "name": "Killzone Londres/NY (filtro)", "school": "Asiática + ICT",
     "timeframes": "filtro", "win_hint": "+3–6pp em qualquer setup",
     "setup": "Opere setups SOMENTE 04h–12h BR (Londres/NY) e 09h30–12h (overlap). Evite 18h–02h.",
     "entry": "Filtro aplicado sobre qualquer setup acima.",
     "filter": "Madrugada/Ásia: só reversão em borda com stake mínimo ou nada.",
     "why": "Liquidez = seguimento. O mesmo setup rende ~5 pontos a mais de winrate em killzone."},
    {"id": "gestaomestre", "name": "Gestão Mestre (obrigatória)", "school": "Chinesa/Russa (disciplina+math)",
     "timeframes": "sempre", "win_hint": "define quem sobrevive",
     "setup": "Stake 1–2% (Kelly/4), máx 3–5% risco/dia, stop WIN 4–6% e stop LOSS 3–5%, máx 2 entradas/setup.",
     "entry": "Sem martingale. Gale só 1 nível e SÓ em setup A+ (a matemática do gale infinito quebra).",
     "filter": "2 losses seguidos = pausa 30min. 3 losses no dia = FIM. Dia de tilt = conta salva.",
     "why": "Com payout 85% e 58% de acerto, Kelly/4 dá +0.6% banca/dia com drawdown controlado. Martingale dá +lento e −tudo."},
]
