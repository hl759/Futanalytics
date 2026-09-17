"""Modelo de gols: coerência das probabilidades, mistura com o mercado e seleção."""
import pytest

from app import model as md

# grupos que precisam somar 1 (ou o complemento exato)
PARES = [("over_0.5", "under_0.5"), ("over_1.5", "under_1.5"), ("over_2.5", "under_2.5"),
         ("over_3.5", "under_3.5"), ("btts_yes", "btts_no"),
         ("ht_0.5", "under_ht_0.5"), ("at_0.5", "under_at_0.5"),
         ("ht_1.5", "under_ht_1.5"), ("at_1.5", "under_at_1.5")]


def test_probabilidades_somam_um(sample_analysis):
    m = sample_analysis["markets"]
    for a, b in PARES:
        if a in m and b in m:
            assert m[a] + m[b] == pytest.approx(1.0, abs=1e-6), (a, b)
    assert m["home"] + m["draw"] + m["away"] == pytest.approx(1.0, abs=1e-6)
    assert m["dc_1x"] == pytest.approx(m["home"] + m["draw"], abs=1e-6)
    assert m["over_2.5"] > m["over_3.5"] > m["over_4.5"] if "over_4.5" in m else True


def test_markets_sao_monotonicos_em_gols(sample_analysis):
    m = sample_analysis["markets"]
    assert m["over_0.5"] > m["over_1.5"] > m["over_2.5"] > m["over_3.5"]
    assert m["ht_0.5"] > m["ht_1.5"]
    assert m["at_0.5"] > m["at_1.5"]
    # total esperado coerente com as probabilidades de over
    assert 0.8 < sample_analysis["expected_goals_total"] < 5.5


def test_odds_justas_sao_inverso_da_probabilidade(sample_analysis):
    for k, p in sample_analysis["markets"].items():
        if p > 0.01:
            assert sample_analysis["fair_odds"][k] == pytest.approx(1 / p, abs=0.02)


def test_blend_extremos(sample_analysis):
    from app import market as mk

    modelo = sample_analysis["markets"]
    odds = {"over_2.5": 2.30, "under_2.5": 1.75, "home": 3.00, "draw": 3.40, "away": 2.35}
    # peso 1 = modelo puro
    so_modelo = md.blend_markets(modelo, odds, model_weight=1.0)
    assert so_modelo["over_2.5"] == pytest.approx(modelo["over_2.5"], abs=1e-6)
    # peso 0 = mercado puro (devigado e renormado dentro do grupo)
    so_mercado = md.blend_markets(modelo, odds, model_weight=0.0)
    assert so_mercado["over_2.5"] == pytest.approx(mk.devig([2.30, 1.75])[0], abs=1e-6)
    assert so_mercado["home"] + so_mercado["draw"] + so_mercado["away"] == pytest.approx(1.0, abs=1e-6)
    # a mistura fica entre os dois
    meio = md.blend_markets(modelo, odds, model_weight=0.5)
    q = mk.devig([2.30, 1.75])[0]
    assert min(modelo["over_2.5"], q) <= meio["over_2.5"] <= max(modelo["over_2.5"], q)
    # mercados sem os dois lados ficam intactos
    assert so_mercado["over_1.5"] == pytest.approx(modelo["over_1.5"], abs=1e-6)


def test_market_candidates_ordena_por_crescimento_e_recusa_preco_curto(sample_analysis):
    # par ht_0.5/under_ht_0.5 com valor no lado "marca": preço 1.45 contra
    # 3.00 do outro lado (margem normal) e modelo em 72.5%
    p_marca = sample_analysis["markets"]["ht_0.5"]
    odds = {"ht_0.5": 1.45, "under_ht_0.5": 3.00}
    cards = md.market_candidates(sample_analysis, odds)
    assert cards, "deveria haver candidatos"
    assert all(c["market"] in md.CANDIDATE_MARKETS for c in cards)
    growths = [c["growth"] for c in cards]
    assert growths == sorted(growths, reverse=True)
    marca = next(c for c in cards if c["market"] == "ht_0.5")
    assert marca["take"] is True
    assert marca["ev"] == pytest.approx(p_marca * 1.45 - 1, abs=1e-3)
    assert marca["edge"] > 0.015
    # preço ruim (abaixo do justo) não pode ser aceito
    ruim = md.market_candidates(sample_analysis, {"ht_0.5": 1.05, "under_ht_0.5": 12.0})
    assert all(not c["take"] for c in ruim)
    # sem o outro lado do mercado, exige 2 pp extra de EV
    so_um_lado = md.market_candidates(sample_analysis, {"ht_0.5": 1.45})
    card_um = next(c for c in so_um_lado if c["market"] == "ht_0.5")
    assert card_um["min_ev_used"] == pytest.approx(0.045)


def test_market_candidates_respeita_lista_de_mercados(sample_analysis):
    cards = md.market_candidates(sample_analysis, {"home": 2.5, "draw": 3.4, "away": 3.0},
                                 markets=("home", "draw", "away"))
    assert {c["market"] for c in cards} <= {"home", "draw", "away"}


def test_pick_value_e_watchlist(sample_analysis):
    bom = {"ht_0.5": 1.45, "under_ht_0.5": 3.00}
    card = md.pick_value(sample_analysis, bom)
    assert card is not None and card["take"] is True and card["market"] == "ht_0.5"
    sem_valor = {"over_2.5": 3.00, "under_2.5": 1.40}
    assert md.pick_value(sample_analysis, sem_valor) is None
    lista = md.watchlist(sample_analysis, bom)
    assert all(not c["take"] for c in lista)
    assert len(lista) <= 3


def test_policy_cobre_modos_usados():
    for modo in ("singles", "legs", "resultado"):
        assert modo in md.POLICY
        assert md.POLICY[modo]["min_odd"] < md.POLICY[modo]["max_odd"]
        assert md.POLICY[modo]["min_prob"] < md.POLICY[modo]["max_prob"]


def test_amostra_insuficiente_nao_quebra():
    from app.model import TeamSample, analyze_match

    magra = TeamSample("Time Novo", [(5, True, 1, 1), (12, False, 0, 2)])
    a = analyze_match(magra, magra)
    assert a["markets"]["home"] + a["markets"]["draw"] + a["markets"]["away"] == pytest.approx(1.0, abs=1e-6)
    assert 0.0 <= a["confidence"] <= 1.0
