"""Modelo conjunto por liga: recuperação de força, blindagem contra vazamento e clamps."""
import datetime as dt

import pytest

from app import joint


def _league(strong_attack: float = 2.3, fraca_defesa: float = 2.0, n_teams: int = 10,
            start: dt.date | None = None) -> list[dict]:
    """Liga sintética: T0 tem ataque forte e T9 defesa fraca; resto é médio."""
    start = start or dt.date(2023, 8, 1)
    times = [f"T{i}" for i in range(n_teams)]
    atk = dict.fromkeys(times, 1.0)
    dfn = dict.fromkeys(times, 1.0)
    atk["T0"], dfn["T9"] = strong_attack, fraca_defesa
    atk["T9"], dfn["T0"] = 0.6, 0.6
    out = []
    dia = 0
    for i, casa in enumerate(times):
        for j, fora in enumerate(times):
            if i == j:
                continue
            lam_c = 1.35 * atk[casa] * dfn[fora]
            lam_f = 1.10 * atk[fora] * dfn[casa]
            out.append({
                "date": start + dt.timedelta(days=dia * 3),
                "home": casa, "away": fora,
                "hg": round(lam_c), "ag": round(lam_f),
                "xgh": None, "xga": None,
            })
            dia += 1
    return out


def test_fit_e_predict_recuperam_forca():
    matches = _league()
    on_date = dt.date(2024, 6, 1)
    model = joint.fit_league(matches, on_date)
    assert model is not None
    assert model["n_matches"] >= 25
    assert len(model["teams"]) == 10
    lam_h, lam_a = joint.predict_lambdas(model, "T0", "T9")
    lam_h2, lam_a2 = joint.predict_lambdas(model, "T9", "T0")
    # T0 contra T9 em casa deve ser bem mais gols que o inverso
    assert lam_h > lam_h2
    assert lam_a < lam_a2
    assert 0.15 <= lam_h <= 4.5 and 0.15 <= lam_a <= 4.5


def test_sem_amostra_suficiente_retorna_none():
    poucos = _league(n_teams=4)[:6]
    assert joint.fit_league(poucos, dt.date(2024, 6, 1)) is None


def test_blindagem_contra_vazamento():
    """Jogos futuros não podem influenciar o ajuste de uma data passada."""
    matches = _league()
    corte = dt.date(2024, 1, 1)
    antes = [m for m in matches if m["date"] < corte]
    futuro = [dict(m, hg=9, ag=9) for m in matches if m["date"] >= corte]
    assert futuro, "o cenário de teste precisa de jogos futuros"
    modelo_a = joint.fit_league(matches, corte)
    modelo_b = joint.fit_league(antes, corte)
    assert modelo_a is not None and modelo_b is not None
    la_a, lb_a = joint.predict_lambdas(modelo_a, "T0", "T9")
    la_b, lb_b = joint.predict_lambdas(modelo_b, "T0", "T9")
    # mutar/remover o futuro não muda o que foi ajustado com o passado
    assert la_a == pytest.approx(la_b, abs=1e-9)
    assert lb_a == pytest.approx(lb_b, abs=1e-9)
    # e os jogos futuros de fato existiam na lista completa
    assert any(m["date"] >= corte for m in matches)


def test_jogos_antigos_demais_sao_descartados():
    antigos = [dict(m, date=m["date"] - dt.timedelta(days=joint.MAX_AGE_DAYS + 30))
               for m in _league()]
    assert joint.fit_league(antigos, dt.date(2024, 6, 1)) is None


def test_xg_preferido_quando_disponivel():
    matches = _league()
    for m in matches:
        m["xgh"] = m["hg"] + 0.5      # xG sistematicamente maior
        m["xga"] = m["ag"] + 0.5
    model = joint.fit_league(matches, dt.date(2024, 6, 1), xg_preferred=True)
    assert model["using_xg"] is True
    lam_h, lam_a = joint.predict_lambdas(model, "T0", "T9")
    assert lam_h + lam_a > 3.0        # reflete o xG inflado

    sem_xg = joint.fit_league(matches, dt.date(2024, 6, 1), xg_preferred=False)
    assert sem_xg["using_xg"] is False


def test_league_env_e_quality():
    model = joint.fit_league(_league(), dt.date(2024, 6, 1))
    env = joint.league_env(model)
    assert env["n_matches"] >= 25
    assert 0.5 < env["avg_total"] < 6.0
    q = joint.ratings_quality(model)
    assert q["n_matches"] >= 25
    assert q["spread"] > 0
