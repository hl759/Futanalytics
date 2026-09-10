"""Calibração de probabilidades por regressão isotônica (PAVA).

Por que: um modelo pode ser BOM EM RANKING (times maiores têm prob maior mesmo)
e ruim EM MAGNITUDE (dizer 0.62 onde a frequência real é 0.52). EV calculado
sobre probabilidade mal calibrada é ficção. A calibração isotônica aprende,
a partir de temporadas passadas, um mapeamento monotônico prob_prevista ->
prob_real, e o aplica às previsões novas.

Treino e curvas ficam em app/calibration_data.json, gerado por:
    python -m app.backtest --fit-calibration
O arquivo viaja com o código; o app aplica na hora da análise.

As curvas são por família de mercado e por MODO (xg / goals), porque a
distribuição das probabilidades muda conforme o insumo:
    "over_2.5", "btts_yes", "home", "away"  ×  ("xg" | "goals")
Complementos (under_N.5, btts_no, draw, dupla chance) são derivados por
complemento para manter soma = 1 dentro de cada grupo.
"""
from __future__ import annotations

import json
from pathlib import Path

CALIB_PATH = Path(__file__).resolve().parent / "calibration_data.json"

# mercados calibrados diretamente; os demais saem por complemento/grupo
DIRECT_MARKETS = ["over_2.5", "btts_yes", "home", "away", "over_1.5", "over_3.5"]
MODES = ["xg", "goals"]


def _pava(pairs):
    """Pool Adjacent Violators: isotonic regression com pesos.

    pairs: lista (p_previsto, y_real, peso). Retorna lista de (x, y) monotônica
    não decrescente nos centroides.
    """
    blocks = []  # cada bloco: [soma_x, soma_ponderada_y, soma_pesos, xs]
    for x, y, w in sorted(pairs, key=lambda t: t[0]):
        if w <= 0:
            continue
        blocks.append([x, y * w, w, [x]])
        while len(blocks) >= 2:
            b1, b2 = blocks[-2], blocks[-1]
            m1 = b1[1] / b1[2]
            m2 = b2[1] / b2[2]
            if m1 <= m2:
                break
            # violação de monotonia: funde os blocos
            blocks[-2] = [b1[0] + b2[0], b1[1] + b2[1], b1[2] + b2[2], b1[3] + b2[3]]
            blocks.pop()
    curve = []
    for sx, swy, sw, xs in blocks:
        curve.append((sx / len(xs), swy / sw))
    return curve


def _interp(curve, x):
    """Avalia a curva escalonada em x com clamp nas bordas."""
    if not curve:
        return x
    xs = [c[0] for c in curve]
    ys = [c[1] for c in curve]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    # interpolação linear entre centroides (suaviza o degrau)
    x0, y0 = curve[lo]
    x1, y1 = curve[hi]
    if x1 - x0 < 1e-9:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


class Calibrator:
    """Mapeamento isotônico p_previsto -> p_calibrada, com shrinkage amostral.

    Com poucos dados, a curva vira identidade (não calibrar é melhor do que
    calibrar com ruído): o blend com identidade cresce com sqrt(n).
    """

    def __init__(self, curve=None, n=0):
        self.curve = curve or []
        self.n = n

    def fit(self, pairs):
        self.curve = _pava(pairs)
        self.n = sum(p[2] for p in pairs)
        return self

    def transform(self, p):
        if not self.curve or self.n < 300:
            return p
        c = _interp(self.curve, p)
        # shrinkage para a identidade quando a amostra é modesta
        w = min(1.0, self.n / 2000.0)
        return p * (1 - w) + c * w

    def to_json(self):
        return {"curve": self.curve, "n": round(self.n, 1)}

    @classmethod
    def from_json(cls, d):
        return cls([tuple(x) for x in d.get("curve", [])], d.get("n", 0))


def load_all(path: Path | None = None) -> dict:
    """Retorna {modo: {mercado: Calibrator}} ou {} se não houver arquivo."""
    p = path or CALIB_PATH
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for mode, markets in raw.items():
        out[mode] = {mk: Calibrator.from_json(d) for mk, d in markets.items()}
    return out


def save_all(all_cal: dict, path: Path | None = None):
    p = path or CALIB_PATH
    raw = {mode: {mk: c.to_json() for mk, c in markets.items()} for mode, markets in all_cal.items()}
    p.write_text(json.dumps(raw, indent=1))


def apply_calibration(markets: dict, calibrators: dict) -> dict:
    """Aplica as curvas preservando a soma de cada grupo de resultados.

    calibrators: {mercado: Calibrator} do modo correspondente (xg ou goals).
    """
    if not calibrators:
        return markets
    out = dict(markets)

    # Over/Under por linha: calibra o over, deriva o under
    for line in ("0.5", "1.5", "2.5", "3.5"):
        mk = f"over_{line}"
        c = calibrators.get(mk)
        if c and mk in markets:
            po = min(max(c.transform(markets[mk]), 0.01), 0.99)
            out[mk] = po
            out[f"under_{line}"] = 1 - po

    # BTTS
    c = calibrators.get("btts_yes")
    if c and "btts_yes" in markets:
        pb = min(max(c.transform(markets["btts_yes"]), 0.01), 0.99)
        out["btts_yes"] = pb
        out["btts_no"] = 1 - pb

    # 1X2: calibra casa e fora, empate por complemento (clamp + renorm)
    ch_c, ca_c = calibrators.get("home"), calibrators.get("away")
    if ch_c and ca_c and all(k in markets for k in ("home", "draw", "away")):
        ch = min(max(ch_c.transform(markets["home"]), 0.01), 0.97)
        ca = min(max(ca_c.transform(markets["away"]), 0.01), 0.97)
        cd = 1 - ch - ca
        if cd < 0.01:  # excesso raro; devolve massa proporcional
            deficit = 0.01 - cd
            ch = max(ch - deficit / 2, 0.01)
            ca = max(ca - deficit / 2, 0.01)
            cd = 1 - ch - ca
        s = ch + cd + ca
        out["home"], out["draw"], out["away"] = ch / s, cd / s, ca / s
        out["dc_1x"] = out["home"] + out["draw"]
        out["dc_x2"] = out["draw"] + out["away"]
        out["dc_12"] = out["home"] + out["away"]
    return out
