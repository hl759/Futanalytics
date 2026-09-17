"""Combinadas: probabilidade conjunta, correlação, matemática do bilhete e escada."""
import math

import pytest

from app import parlay as pl


def _leg(prob, odd, *, league="E0", family="gols", market="over_2.5"):
    return {"prob": prob, "odd": odd, "league": league, "family": family,
            "market": market, "growth": 0.01, "ev": prob * odd - 1}


def test_joint_prob_independencia():
    assert pl.joint_prob([0.5, 0.5], 0.0) == pytest.approx(0.25)
    assert pl.joint_prob([0.7, 0.7, 0.7], 0.0) == pytest.approx(0.343)
    assert pl.joint_prob([1.0, 1.0], 0.0) == pytest.approx(1.0)
    # casos degenerados: uma perna devolve a própria probabilidade
    assert pl.joint_prob([0.5], 0.0) == pytest.approx(0.5)


def test_joint_prob_correlacao_positiva_aumenta_probabilidade():
    # correlação positiva => eventos andam juntos => P(todos) sobe
    assert pl.joint_prob([0.7, 0.7], 0.10) > 0.49
    assert pl.joint_prob([0.7, 0.7], -0.10) < 0.49


@pytest.mark.parametrize("probs,rho", [((0.7, 0.7), 0.06), ((0.8, 0.65), 0.06),
                                       ((0.9, 0.85), 0.06), ((0.6, 0.75), 0.03)])
def test_joint_prob_bate_com_simulacao_monte_carlo(probs, rho):
    """A expansão de 1ª ordem precisa bater com a simulação gaussiana (2 pernas = exata)."""
    import random

    zs = [pl.norm_ppf(p) for p in probs]
    rng = random.Random(7)
    n = 200_000
    hits = 0
    for _ in range(n):
        x = rng.gauss(0, 1)
        y = rho * x + math.sqrt(max(1 - rho * rho, 0.0)) * rng.gauss(0, 1)
        if all(v <= z for v, z in zip((x, y), zs, strict=True)):
            hits += 1
    sim = hits / n
    assert pl.joint_prob(list(probs), rho) == pytest.approx(sim, abs=0.01)


def test_norm_ppf_inversa_da_cdf():
    for p in (0.05, 0.25, 0.5, 0.75, 0.95):
        assert pl._norm_cdf(pl.norm_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_correlacao_por_contexto():
    mesma_liga = _leg(0.7, 1.5, league="E0", family="gols")
    mesma_liga_outra_familia = _leg(0.7, 1.5, league="E0", family="resultado")
    outra_liga = _leg(0.7, 1.5, league="SP1", family="gols")
    assert pl.correlation_for(mesma_liga, mesma_liga_outra_familia) > 0
    assert (pl.correlation_for(mesma_liga, mesma_liga_outra_familia)
            >= pl.correlation_for(mesma_liga, outra_liga))
    assert pl.correlation_for(None, mesma_liga) == 0.0


def test_slip_math_produto_e_cenario_conservador():
    legs = [_leg(0.75, 1.40), _leg(0.70, 1.50)]
    s = pl.slip_math(legs)
    assert s["legs"] == 2
    assert s["combined_odd"] == pytest.approx(1.40 * 1.50, abs=0.01)
    assert s["prob_independent"] == pytest.approx(0.75 * 0.70, abs=1e-4)
    assert s["prob_joint"] >= s["prob_independent"]      # correlação ≥ 0
    assert s["ev_independent"] == pytest.approx(0.75 * 0.70 * 1.40 * 1.50 - 1, abs=1e-3)
    # stake do bilhete é dimensionada pelo cenário conservador (independência)
    assert 0 <= s["stake_pct"] <= 3.0
    assert s["breakeven_odd"] == pytest.approx(1 / (0.75 * 0.70), abs=0.01)


def test_slip_math_sem_valor_nao_sugere_stake():
    legs = [_leg(0.60, 1.30), _leg(0.60, 1.30)]
    s = pl.slip_math(legs)
    assert s["ev_independent"] < 0
    assert s["stake_pct"] == 0.0


def test_build_exige_minimo_de_pernas_e_teto_de_odd():
    fracas = [_leg(0.62, 1.35)]
    assert pl.build(fracas)["available"] is False
    sem_valor = [_leg(0.62, 1.33), _leg(0.64, 1.33)]
    assert pl.build(sem_valor)["available"] is False
    boas = [_leg(0.75, 1.55, market="over_2.5", league="E0"),
            _leg(0.72, 1.60, market="btts_yes", league="SP1"),
            _leg(0.70, 1.62, market="over_1.5", league="I1")]
    out = pl.build(boas)
    assert out["available"] is True
    assert 2 <= len(out["legs"]) <= pl.DEFAULT_MAX_LEGS
    assert out["slip"]["combined_odd"] <= pl.DEFAULT_MAX_COMBINED_ODD


def test_build_nao_estoura_teto_de_odd_combinada():
    # quatro pernas a @1,9 = 13,03 >> teto de 8: a montagem não pode escolher esse tamanho
    legs = [_leg(0.60, 1.90, market=f"m{i}", league="E0") for i in range(4)]
    out = pl.build(legs, max_legs=4)
    if out["available"]:
        assert out["slip"]["combined_odd"] <= pl.DEFAULT_MAX_COMBINED_ODD


def test_ladder_cobre_de_um_a_quatro_e_decresce_a_probabilidade():
    legs = [_leg(0.78, 1.55, market=f"m{i}", league="E0") for i in range(5)]
    escada = pl.ladder(legs, max_legs=4)
    assert [s["k"] for s in escada] == [1, 2, 3, 4]
    probs = [s["prob_independent"] for s in escada]
    assert probs == sorted(probs, reverse=True)
    assert all(s["combined_odd"] > 1 for s in escada)


def test_exposicao_diaria_respeita_teto():
    legs = [_leg(0.75, 1.55), _leg(0.72, 1.60, league="SP1")]
    bilhete = pl.build(legs)
    exp = pl.daily_exposure([bilhete["slip"]], singles_pct=4.0, daily_cap_pct=6.0)
    assert exp["total_pct"] == pytest.approx(4.0 + bilhete["slip"]["stake_pct"], abs=1e-6)
    assert exp["within_cap"] is (exp["total_pct"] <= 6.0)
    assert exp["headroom_pct"] >= 0
