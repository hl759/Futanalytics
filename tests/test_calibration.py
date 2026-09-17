"""Calibração isotônica: monotonicidade, correção de excesso de confiança e grupos."""
import json

import pytest

from app import calibration as calib


def _overconfident_pairs(n=600, seed=3):
    """Modelo exagerado: diz 0.8 quando a frequência real é ~0.68."""
    import random
    rng = random.Random(seed)
    pairs = []
    for _ in range(n):
        p = rng.uniform(0.1, 0.9)
        q = 0.5 + (p - 0.5) * 0.7           # realidade mais conservadora
        y = 1.0 if rng.random() < q else 0.0
        pairs.append((p, y, 1.0))
    return pairs


def test_transform_corrige_excesso_de_confianca_e_e_monotona():
    c = calib.Calibrator().fit(_overconfident_pairs())
    assert c.n >= 600
    altos = [c.transform(p) for p in (0.60, 0.70, 0.80, 0.90)]
    assert altos == sorted(altos)                       # monotônica
    assert altos[0] < 0.60 + 0.05                       # puxa para o centro
    baixos = [c.transform(p) for p in (0.10, 0.20, 0.30)]
    assert baixos == sorted(baixos)
    assert baixos[-1] > 0.30 - 0.05


def test_amostra_pequena_vira_identidade():
    c = calib.Calibrator().fit([(0.8, 1.0, 1.0), (0.2, 0.0, 1.0)])
    assert c.transform(0.8) == pytest.approx(0.8)
    assert c.transform(0.2) == pytest.approx(0.2)


def test_apply_calibration_preserva_grupos():
    cals = {
        "over_2.5": calib.Calibrator().fit(_overconfident_pairs()),
        "btts_yes": calib.Calibrator().fit(_overconfident_pairs(seed=5)),
        "home": calib.Calibrator().fit(_overconfident_pairs(seed=7)),
        "away": calib.Calibrator().fit(_overconfident_pairs(seed=11)),
    }
    markets = {
        "over_2.5": 0.62, "under_2.5": 0.38,
        "over_1.5": 0.80, "under_1.5": 0.20,
        "btts_yes": 0.60, "btts_no": 0.40,
        "home": 0.55, "draw": 0.26, "away": 0.19,
    }
    out = calib.apply_calibration(markets, cals)
    assert out["over_2.5"] + out["under_2.5"] == pytest.approx(1.0)
    assert out["btts_yes"] + out["btts_no"] == pytest.approx(1.0)
    assert out["home"] + out["draw"] + out["away"] == pytest.approx(1.0)
    # over_1.5 não tem curva: fica intacto (e o under não é recalculado)
    assert out["over_1.5"] == pytest.approx(0.80)
    # dc_* continuam coerentes com o 1X2 calibrado
    assert out["dc_1x"] == pytest.approx(out["home"] + out["draw"], abs=1e-6)


def test_apply_calibration_sem_curvas_nao_muda_nada():
    markets = {"over_2.5": 0.62, "under_2.5": 0.38}
    assert calib.apply_calibration(markets, {}) == markets


def test_salvar_e_carregar_com_proveniencia(tmp_path):
    cals = {"joint": {"over_2.5": calib.Calibrator().fit(_overconfident_pairs())}}
    path = tmp_path / "cal.json"
    calib.save_all(cals, path, seasons=["1920"], divs=["E0"], model_weight=0.25)
    meta = calib.load_meta(path)
    assert meta["seasons"] == ["1920"]
    assert "fitted_at" in meta
    loaded = calib.load_all(path)
    assert set(loaded) == {"joint"}          # _meta não vira "modo"
    assert loaded["joint"]["over_2.5"].n == pytest.approx(600)
    assert loaded["joint"]["over_2.5"].transform(0.8) == pytest.approx(
        cals["joint"]["over_2.5"].transform(0.8))
    # arquivo é JSON legível e contém a curva
    raw = json.loads(path.read_text())
    assert raw["_meta"]["seasons"] == ["1920"]
    assert len(raw["joint"]["over_2.5"]["curve"]) >= 2


def test_arquivo_inexistente_ou_corrompido(tmp_path):
    assert calib.load_all(tmp_path / "nao_existe.json") == {}
    quebrado = tmp_path / "quebrado.json"
    quebrado.write_text("{isso não é json")
    assert calib.load_all(quebrado) == {}
    assert calib.load_meta(quebrado) == {}
