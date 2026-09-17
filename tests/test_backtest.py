"""Backtest: carga de dados, métricas, simulação de banca e regressão do alpha."""
import datetime as dt

import pytest

from app import backtest as bt

CSV = """Date,HomeTeam,AwayTeam,FTHG,FTAG,B365H,B365D,B365A,B365>2.5,B365<2.5,B365CH,B365CD,B365CA,B365C>2.5,B365C<2.5
12/08/2023,Arsenal,Chelsea,2,1,1.80,3.60,4.50,1.72,2.10,1.75,3.70,4.60,1.70,2.15
19/08/2023,Chelsea,Arsenal,0,0,2.60,3.20,2.75,1.95,1.90,2.50,3.25,2.85,1.92,1.92
lista invalida,,,,,x,y,z,,,,,,,
26/08/2023,Liverpool,Everton,3,1,1.50,4.20,6.50,1.40,3.00,1.45,4.40,7.00,1.38,3.10
"""


@pytest.fixture()
def fd_env(tmp_path, monkeypatch):
    season_dir = tmp_path / "data" / "2324"
    season_dir.mkdir(parents=True)
    (season_dir / "E0.csv").write_text(CSV, encoding="utf-8")
    monkeypatch.setenv("FUTA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_load_fd_season_le_odds_de_abertura_e_fechamento(fd_env):
    jogos = bt.load_fd_season("E0", "2324")
    assert len(jogos) == 3                       # a linha inválida é descartada
    primeiro = jogos[0]
    assert primeiro["home"] == "Arsenal" and primeiro["away"] == "Chelsea"
    assert primeiro["hg"] == 2 and primeiro["ag"] == 1
    assert primeiro["date"] == dt.date(2023, 8, 12)
    assert primeiro["odds"]["home"] == pytest.approx(1.80)
    assert primeiro["closing"]["home"] == pytest.approx(1.75)
    assert set(primeiro["odds"]) == {"home", "draw", "away", "over_2.5", "under_2.5"}
    assert jogos == sorted(jogos, key=lambda m: m["date"])


def test_load_fd_season_inexistente_nao_quebra(fd_env, monkeypatch):
    monkeypatch.setenv("FUTA_DATA_DIR", str(fd_env / "data"))
    assert bt.load_fd_season("SP1", "2324") == []


def _resultado(**kw):
    base = {
        "date": dt.date(2024, 1, 10), "league": "E0", "season": "2324", "hg": 2, "ag": 1,
        "odds": {"home": 1.90, "draw": 3.40, "away": 4.20, "over_2.5": 1.75, "under_2.5": 2.10},
        "closing": {"home": 1.80, "draw": 3.50, "away": 4.40, "over_2.5": 1.80, "under_2.5": 2.05},
        "raw": {"home": 0.62, "draw": 0.22, "away": 0.16, "over_2.5": 0.61, "under_2.5": 0.39},
        "calibrated": {"home": 0.60, "draw": 0.23, "away": 0.17, "over_2.5": 0.60, "under_2.5": 0.40},
        "probs": {"home": 0.58, "draw": 0.24, "away": 0.18, "over_2.5": 0.57, "under_2.5": 0.43},
        "n_eff": 30.0,
    }
    base.update(kw)
    return base


def test_outcome_e_metricas():
    r = _resultado()
    assert bt.outcome(r, "home") == 1.0
    assert bt.outcome(r, "away") == 0.0
    assert bt.outcome(r, "over_2.5") == 1.0     # 3 gols
    assert bt.outcome(r, "under_2.5") == 0.0
    # Brier de previsão perfeita é zero; constante 0.5 contra tudo dá 0.25
    assert bt.brier([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert bt.brier([0.5, 0.5], [1.0, 0.0]) == pytest.approx(0.25)
    assert bt.logloss([0.5, 0.5], [1.0, 0.0]) == pytest.approx(0.693147, abs=1e-5)


def test_devig_market_usa_abertura_ou_fechamento():
    r = _resultado()
    q = bt.devig_market(r, "home")
    qc = bt.devig_market(r, "home", source="closing")
    assert 0 < q < 1 and 0 < qc < 1
    assert q != qc
    # probabilidade do favorito é maior que a implícita bruta com margem
    assert q < 1 / r["odds"]["home"]
    assert bt.devig_market({"odds": {}, "closing": {}}, "home") is None


def test_simulate_bets_conta_roi_e_clv():
    ganha = {"prob": 0.60, "odd": 2.00, "y": 1.0, "closing_odd": 1.80, "n_eff": 40}
    perde = {"prob": 0.60, "odd": 2.00, "y": 0.0, "closing_odd": 2.20, "n_eff": 40}
    sim = bt.simulate_bets([ganha, perde])
    assert sim["n"] == 2
    assert sim["hit"] == pytest.approx(50.0)
    assert sim["roi"] == pytest.approx(0.0)          # +1 unidade e −1 unidade
    assert sim["clv_mean"] == pytest.approx(100 * ((2.0 / 1.8 - 1) + (2.0 / 2.2 - 1)) / 2, abs=0.01)
    assert sim["clv_beat"] == pytest.approx(50.0)
    assert sim["kelly_max_dd"] >= 0


def test_bootstrap_ci_da_media():
    vals = [1.0] * 50
    lo, hi = bt.bootstrap_ci(vals)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)
    lo2, hi2 = bt.bootstrap_ci([-1.0, 1.0] * 25)
    assert lo2 < 0 < hi2


def test_ols_recupera_inclinacao_e_erro_padrao():
    xs = [0.01 * i for i in range(-20, 20)]
    ys = [0.5 * x + 0.01 for x in xs]
    st = bt.ols(xs, ys)
    assert st["n"] == 40
    assert st["beta"] == pytest.approx(0.5, abs=1e-9)
    assert st["alpha"] == pytest.approx(0.01, abs=1e-9)
    assert st["r2"] == pytest.approx(1.0, abs=1e-9)
    assert st["t"] != 0
    # amostra insuficiente: devolve só o tamanho (sem beta/t) — quem chama trata
    assert bt.ols([1.0], [1.0]) == {"n": 1}


def test_politica_valor_so_aposta_com_valor():
    # over 2.5 a 2.10 com modelo em 57% e o outro lado a 1.80 => valor claro
    odds = {**_resultado()["odds"], "over_2.5": 2.10, "under_2.5": 1.80}
    probs = {**_resultado()["probs"], "over_2.5": 0.57, "under_2.5": 0.43}
    pick = bt.policy_value(_resultado(odds=odds, probs=probs))
    assert pick is not None and pick["market"] == "over_2.5"
    assert pick["ev"] > 0.02
    # preço curto (1.60) não gera entrada no mesmo lado
    curto = {**_resultado()["odds"], "over_2.5": 1.60, "under_2.5": 2.30}
    ruim = bt.policy_value(_resultado(odds=curto, probs=probs))
    assert ruim is None or ruim["market"] != "over_2.5"


def test_politica_valor_restrita_a_um_mercado():
    odds = {**_resultado()["odds"], "home": 2.00}
    probs = {**_resultado()["probs"], "home": 0.60}
    r = _resultado(odds=odds, probs=probs)
    assert bt.policy_value(r) is None                 # 1X2 fora do cardápio padrão
    pick = bt.policy_value(r, only=("home",))
    assert pick is not None and pick["market"] == "home"


def test_politica_oraculo_usa_fechamento():
    # fechamento implica 60% e a abertura paga 2.00 => EV de 20% na abertura
    r = _resultado(odds={**_resultado()["odds"], "home": 2.00},
                   closing={**_resultado()["closing"], "home": 1.80},
                   raw={**_resultado()["raw"], "home": 0.59},
                   calibrated={**_resultado()["calibrated"], "home": 0.59})
    pick = bt.policy_closing_oracle(r)
    assert pick is not None
    assert pick["market"] == "home"
    assert pick["ev"] == pytest.approx(0.5332 * 2.00 - 1, abs=0.02)


def test_politica_mais_provavel_respeita_faixa():
    r = _resultado(probs={"ht_0.5": 0.80, "over_1.5": 0.70, "home": 0.6, "over_2.5": 0.55},
                   odds={**_resultado()["odds"], "ht_0.5": 1.30, "over_1.5": 1.45})
    pick = bt.policy_most_probable(r)
    assert pick is not None
    assert pick["market"] in {"ht_0.5", "over_1.5", "home", "over_2.5"}


def test_collect_bets_descarta_ev_absurdo():
    r = _resultado(odds={**_resultado()["odds"], "home": 3.00},
                   probs={**_resultado()["probs"], "home": 0.90})
    bets = bt.collect_bets([r], bt.policy_value, max_ev=0.25)
    assert all(b["ev"] <= 0.25 for b in bets)


def test_estimate_correlation_sem_dados():
    corr = bt.estimate_correlation([])
    assert corr.get("n", 0) == 0
