"""API FastAPI: rotas de leitura, fluxo de bilhetes, odds, backup e token."""
import pytest


def test_version_health_e_frontend(client):
    v = client.get("/api/version").json()
    assert v["version"]
    assert client.get("/api/health").json()["ok"] is True
    html = client.get("/")
    assert html.status_code == 200
    assert "FutAnalytics" in html.text
    assert client.get("/static/index.html").status_code == 200
    assert "text/html" in client.get("/").headers["content-type"]


def test_settings_leitura_e_escrita(client):
    s = client.get("/api/settings").json()
    assert s["bankroll"] > 0
    assert "policy" in s and "singles" in s["policy"]
    # token e chaves nunca saem em claro
    assert "fd_token" not in s or s["fd_token"] in ("", None)
    assert "has_fd" in s and "has_af" in s

    r = client.post("/api/settings", json={"bankroll": 2500.0, "min_ev": 4.0, "model_weight": 0.4})
    assert r.status_code == 200
    s2 = client.get("/api/settings").json()
    assert s2["bankroll"] == 2500.0
    assert s2["min_ev"] == 4.0
    assert s2["model_weight"] == 0.4
    # volta ao padrão para não afetar os outros testes
    client.post("/api/settings", json={"bankroll": 1000.0, "min_ev": 3.0, "model_weight": 0.5})


def test_settings_rejeita_valores_absurdos(client):
    assert client.post("/api/settings", json={"model_weight": 7.0}).status_code in (400, 422)
    assert client.post("/api/settings", json={"provider": "inexistente"}).status_code in (400, 422)


def test_model_info_e_labels(client):
    info = client.get("/api/model-info").json()
    assert info["version"]
    assert info["calibration_loaded"] is True
    assert "singles" in info["policy"]
    assert info["devig_methods"]
    labels = client.get("/api/labels").json()
    assert labels


def test_day_demo_tem_estrutura_completa(client):
    d = client.get("/api/day", params={"day": "2026-09-18"}).json()
    assert d["provider"] == "demo"
    assert d["fixtures"], "o provedor demo precisa gerar jogos"
    assert d["summary"]["fixtures"] == len(d["fixtures"])
    fx = d["fixtures"][0]
    for campo in ("id", "home", "away", "league", "analysis", "odds"):
        assert campo in fx
    assert fx["analysis"]["markets"]["home"] + fx["analysis"]["markets"]["draw"] + \
        fx["analysis"]["markets"]["away"] == pytest.approx(1.0, abs=1e-6)
    assert "dossier" in fx["analysis"]
    assert isinstance(d["errors"], list)


def test_fluxo_de_odds_com_historico(client):
    fx = client.post("/api/odds/fixture-teste", json={"odds": {"over_2.5": 1.95}})
    assert fx.status_code == 200
    client.post("/api/odds/fixture-teste", json={"odds": {"over_2.5": 1.80}})
    estado = client.get("/api/odds/fixture-teste").json()
    assert estado["odds"]["over_2.5"] == pytest.approx(1.80)
    mov = estado.get("movement", {})
    assert mov["over_2.5"]["direction"] == "caindo"
    assert mov["over_2.5"]["delta_pct"] < 0
    assert client.post("/api/odds/x", json={"odds": {"over_2.5": 0.5}}).status_code == 400


def test_fluxo_de_bilhetes_completo(client):
    aposta = {"match_date": "2026-09-18", "label": "Casa x Fora", "market": "over_2.5",
              "selection": "Mais de 2.5 gols", "odd": 1.95, "stake": 25.0, "prob": 0.58,
              "edge": 0.03}
    r = client.post("/api/bets", json=aposta)
    assert r.status_code == 200
    bid = r.json()["id"]

    lista = client.get("/api/bets").json()
    assert lista["stats"]["total"] >= 1
    assert any(b["id"] == bid for b in lista["bets"])

    # odd de fechamento => CLV calculado contra a odd registrada
    r = client.post(f"/api/bets/{bid}/closing", json={"closing_odd": 1.80})
    assert r.status_code == 200
    linha = next(b for b in client.get("/api/bets").json()["bets"] if b["id"] == bid)
    assert linha["clv"] == pytest.approx(100 * (1.95 / 1.80 - 1), abs=0.01)

    # liquidação: green paga stake*(odd-1)
    r = client.post(f"/api/bets/{bid}/settle", json={"status": "won"})
    assert r.status_code == 200
    linha = next(b for b in client.get("/api/bets").json()["bets"] if b["id"] == bid)
    assert linha["status"] == "won"
    assert linha["profit"] == pytest.approx(25.0 * 0.95, abs=0.01)

    # desfazer a liquidação devolve o bilhete para aberto
    client.post(f"/api/bets/{bid}/settle", json={"status": "open"})
    linha = next(b for b in client.get("/api/bets").json()["bets"] if b["id"] == bid)
    assert linha["status"] == "open" and linha["profit"] == 0

    assert client.post(f"/api/bets/{bid}/settle", json={"status": "banana"}).status_code == 400
    assert client.delete(f"/api/bets/{bid}").status_code == 200
    assert all(b["id"] != bid for b in client.get("/api/bets").json()["bets"])


def test_backup_exporta_e_restaura(client):
    client.post("/api/bets", json={"match_date": "2026-09-19", "label": "A x B",
                                   "market": "over_2.5", "selection": "Mais de 2.5 gols",
                                   "odd": 1.88, "stake": 20.0, "prob": 0.57})
    payload = client.get("/api/backup").json()
    assert payload["app"] == "futanalytics"
    assert payload["schema"] >= 3
    assert isinstance(payload["bets"], list) and payload["bets"]

    restaurado = dict(payload)
    restaurado["bets"] = payload["bets"][:1]
    assert client.post("/api/backup", json=restaurado).status_code == 200
    assert len(client.get("/api/bets").json()["bets"]) == 1
    assert client.post("/api/backup", json={"app": "outro"}).status_code == 400


def test_token_opcional_protege_as_rotas(client, monkeypatch):
    assert client.get("/api/version").status_code == 200
    monkeypatch.setenv("APP_TOKEN", "segredo-de-teste")
    assert client.get("/api/version").status_code == 401
    assert client.get("/api/version", headers={"X-App-Token": "segredo-de-teste"}).status_code == 200
    assert client.get("/api/version", params={"token": "segredo-de-teste"}).status_code == 200
    # a página inicial continua acessível (é o app que pede o token)
    assert client.get("/").status_code == 200
    monkeypatch.delenv("APP_TOKEN")
    assert client.get("/api/version").status_code == 200
