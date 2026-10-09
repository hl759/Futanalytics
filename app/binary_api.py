"""Endpoints BinaryMaster — análise de print + confluência M1/M5."""
from __future__ import annotations

import base64

from fastapi import APIRouter
from pydantic import BaseModel

from .binary_engine import STRATEGIES, VisionFeatures, analyze_chart_image, score_confluence

router = APIRouter(prefix="/api/binary", tags=["binary"])


class AnalyzeIn(BaseModel):
    image_base64: str | None = None
    symbol: str = ""
    expiry: str = "auto"          # auto | M1 | M5
    payout: float = 82.0
    candles_seq: str = ""         # ex: "G R G G R" (antigo -> recente)
    trend: str = "auto"           # auto|forte_alta|alta|lateral|baixa|forte_baixa
    price_pos: str = ""           # topo|meio|fundo
    candle_body: str = ""         # grande|medio|pequeno|doji
    wick_top: str = ""            # grande|pequeno
    wick_bottom: str = ""         # grande|pequeno
    pattern: str = "auto"
    figure: str = ""
    fib: str = ""
    rsi: float | None = None
    stoch_zone: str = ""
    stoch_cross: str = ""
    ema_stack: str = ""
    macd: str = ""
    bollinger: str = ""
    volatility: str = ""
    volume: str = ""
    sr: str = ""
    smc: str = ""
    smc_detail: str = ""
    session: str = ""
    news: str = ""
    otc: bool = False
    account: str = "demo"


def _decode_image(b64: str | None) -> bytes | None:
    if not b64:
        return None
    s = b64.strip()
    if "," in s and len(s) < 50_000_00:
        # data URL: data:image/png;base64,....
        s = s.split(",", 1)[1]
    try:
        raw = base64.b64decode(s)
    except Exception:
        return None
    if len(raw) > 12 * 1024 * 1024:
        return None
    return raw


@router.get("/strategies")
def strategies():
    return {"strategies": STRATEGIES, "count": len(STRATEGIES)}


@router.post("/analyze")
def analyze(body: AnalyzeIn):
    p = body.model_dump()
    vis: VisionFeatures | None = None
    raw = _decode_image(body.image_base64)
    if raw:
        vis = analyze_chart_image(raw)
        if not vis.ok:
            # visão falhou: segue só com manual, mas avisa
            pass
    elif body.image_base64:
        vis = VisionFeatures(ok=False, error="Imagem inválida ou grande demais (máx 12MB).")
    result = score_confluence(p, vis)
    return result
