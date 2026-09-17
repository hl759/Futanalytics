"""Matemática de mercado: devig, EV, Kelly, alvo de odd e crescimento."""
import math

import pytest

from app import market as mk


def test_devig_proporcional_soma_um():
    qs = mk.devig([2.0, 3.5, 4.0], "proportional")
    assert sum(qs) == pytest.approx(1.0)
    # favorito continua favorito
    assert qs[0] > qs[1] > qs[2]


def test_devig_power_e_shin_removem_margem():
    odds = [1.80, 3.60, 4.50]
    bruto = sum(1 / o for o in odds)  # > 1 (margem embutida)
    assert bruto > 1.0
    for metodo in ("proportional", "power", "shin"):
        qs = mk.devig(odds, metodo)
        assert sum(qs) == pytest.approx(1.0, abs=1e-6), metodo
        assert all(0 < q < 1 for q in qs)
        # nenhuma probabilidade pode superar a implícita bruta
        assert qs[0] <= 1 / odds[0]


def test_devig_metodo_invalido():
    with pytest.raises(ValueError):
        mk.devig([2.0, 2.0], "magic")


def test_ev_edge_breakeven():
    # preço justo: EV zero na odd 1/p
    assert mk.expected_value(0.5, 2.0) == pytest.approx(0.0)
    assert mk.expected_value(0.5, 2.2) == pytest.approx(0.10)
    assert mk.edge(0.55, 0.50) == pytest.approx(0.05)
    assert mk.breakeven_odd(0.4) == pytest.approx(2.5)
    # alvo = odd mínima que ainda entrega o EV desejado
    assert mk.required_odd(0.5, min_ev=0.0) == pytest.approx(2.0)
    assert mk.required_odd(0.5, min_ev=0.10) == pytest.approx(2.2)


def test_kelly_e_incerteza():
    # p=0.55, odd=2.0 -> b=1 -> f* = (p*b - q)/b = 0.10 da banca
    assert mk.kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    # sem vantagem, Kelly cheia é negativa (sinal de "não apostar") e a
    # fração final é zerada — nunca se aposta com EV negativo
    assert mk.kelly_fraction(0.45, 2.0) < 0
    assert mk.stake_fraction(0.45, 2.0)["pct"] == 0.0
    # desconto de incerteza nunca aumenta a stake, e respeita o teto
    cheia = mk.stake_fraction(0.55, 2.0, kelly_mult=1.0, cap=1.0, uncertainty=False)
    incerta = mk.stake_fraction(0.55, 2.0, kelly_mult=1.0, cap=1.0, n_eff=60, uncertainty=True)
    assert incerta["kelly_frac"] < cheia["kelly_frac"]
    capped = mk.stake_fraction(0.80, 3.0, kelly_mult=1.0, cap=0.03, uncertainty=False)
    assert capped["kelly_frac"] == pytest.approx(0.03)


def test_beta_ppf_encolhe_com_pouca_amostra():
    # Beta(0.5n+1, 0.5n+1): o quantil inferior cresce com n (menos incerteza)
    grande = mk.beta_ppf(0.5, 600, 0.30)
    pequeno = mk.beta_ppf(0.5, 20, 0.30)
    assert 0.45 < grande < 0.50
    assert pequeno == pytest.approx(0.42, abs=0.04)
    assert pequeno < grande


def test_evaluate_recusa_ev_negativo_e_preco_curto():
    card = mk.evaluate(0.58, 1.65, 0.62)
    assert card["take"] is False
    assert card["ev"] < 0
    assert any("EV" in r for r in card["reasons"])


def test_evaluate_aceita_valor_na_faixa():
    card = mk.evaluate(0.55, 2.00, 0.50)
    assert card["take"] is True
    assert card["ev"] == pytest.approx(0.10)
    assert card["edge"] == pytest.approx(0.05)
    assert card["required_odd"] == pytest.approx((1 + 0.025) / 0.55, abs=0.01)
    assert card["growth"] > 0


def test_evaluate_flagra_ev_absurdo():
    card = mk.evaluate(0.80, 3.00, 0.55)
    assert card["take"] is False
    assert any("suspeit" in r.lower() for r in card["reasons"])


def test_growth_score_cresce_com_ev():
    a = mk.growth_score(0.55, 2.0, kelly_mult=0.25, uncertainty=False)
    b = mk.growth_score(0.55, 2.2, kelly_mult=0.25, uncertainty=False)
    assert b > a
    assert a > 0
    # sem valor, o crescimento não pode ser positivo
    assert mk.growth_score(0.45, 2.0, uncertainty=False) <= 0


def test_probabilidade_e_odds_consistentes():
    for p in (0.2, 0.5, 0.8):
        assert mk.breakeven_odd(p) * p == pytest.approx(1.0)
    assert math.isclose(mk.breakeven_odd(0.5), 2.0)
