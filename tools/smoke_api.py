"""Smoke test da API inteira, in-process, contra um banco DESCARTÁVEL.

Complementa validate_etapa0.py: aquele valida a MATEMÁTICA sobre dado real
capturado; este valida o CONTRATO HTTP — todo endpoint, inclusive os de
escrita. Existe porque o INSERT de trades nasceu quebrado
(`21 values for 20 columns`) e nenhum teste offline pegou: só apareceu quando
alguém chamou POST /api/trades de verdade.

    .venv/bin/python -m tools.smoke_api
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# O banco é escolhido no IMPORT de app.db, então o env tem que existir antes.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SIGMADESK_DB"] = _tmp.name
os.environ.setdefault("PYTHONHASHSEED", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

fails: list[str] = []
oks: list[str] = []


def check(cond: bool, label: str, detail: str = ""):
    (oks if cond else fails).append(f"{label} {detail}".strip())


def jget(c: TestClient, url: str, **kw):
    r = c.get(url, timeout=180, **kw)
    check(r.status_code == 200, f"GET {url}", f"-> {r.status_code}")
    try:
        return r.json()
    except Exception:
        check(False, f"GET {url} devolveu JSON", f"-> {r.text[:120]}")
        return {}


def main() -> int:
    print("=" * 74)
    print("SMOKE TEST API — SigmaDesk Etapa 0")
    print("=" * 74)
    print(f"banco descartável: {_tmp.name}")
    check(db.DB_PATH == Path(_tmp.name), "SIGMADESK_DB respeitado pelo app.db",
          f"-> {db.DB_PATH}")

    with TestClient(app) as c:
        # ------------------------------------------------------------ leitura
        print("\n[leitura]")
        v = jget(c, "/api/version")
        check(v.get("app") == "SigmaDesk" and v.get("etapa") == 0, "version",
              f"-> {v.get('version')} / {v.get('codename')}")
        h = jget(c, "/api/health")
        check(h.get("ok") is True, "health")
        mi = jget(c, "/api/model-info")
        check("heranca" in mi and "parametros" in mi, "model-info documenta a herança")
        lb = jget(c, "/api/labels")
        check("BTC" in lb.get("currencies", []) and "SOL" not in lb.get("currencies", []),
              "labels: só BTC+ETH no radar (decisão de escopo)", f"-> {lb.get('currencies')}")
        check("iron_condor" in lb.get("structures_defined_risk", []),
              "structures_defined_risk presente")
        check(all(s not in lb.get("structures_defined_risk", [])
                  for s in lb.get("structures_naked", [])),
              "nenhuma estrutura naked classificada como risco definido")

        # ------------------------------------------------------------ settings
        print("\n[settings]")
        s0 = jget(c, "/api/settings")
        check(s0.get("defined_risk_only") is True, "DEFAULT defined_risk_only=True")
        check(s0.get("naked_short_enabled") is False, "DEFAULT naked_short_enabled=False")
        r = c.post("/api/settings", json={"bankroll": 2500, "target_dte": 45}, timeout=60)
        check(r.status_code == 200 and r.json().get("ok"), "POST /api/settings",
              f"-> {r.status_code}")
        s1 = jget(c, "/api/settings")
        check(s1.get("bankroll") == 2500 and s1.get("target_dte") == 45,
              "settings persistiu entre requests", f"-> bankroll {s1.get('bankroll')}")
        # validação: BUG REAL pego aqui. O handler gravava QUALQUER valor —
        # `bankroll: -50` foi aceito e persistido, e como stake_pct =
        # notional/bankroll*100, o sizing do trade seguinte saiu −3108%.
        for bad, why in (({"bankroll": -50}, "capital negativo"),
                         ({"bankroll": 0}, "capital zero (divisão por zero)"),
                         ({"kelly_fraction": 3.0}, "Kelly > 1"),
                         ({"model_weight": -0.2}, "peso de modelo negativo"),
                         ({"stress_rv_multiple": 0.5}, "estresse < 1 não é estresse"),
                         ({"target_dte": 0}, "DTE zero"),
                         ({"max_positions": 0}, "zero posições"),
                         ({"currencies": "BTC,SOL"}, "SOL fora do escopo v1"),
                         ({"portfolio_min_grade": "Z"}, "grade inexistente"),
                         ({"provider_force": "binance"}, "Binance removido (geo-bloqueio)")):
            k = next(iter(bad))
            r = c.post("/api/settings", json=bad, timeout=60)
            check(r.status_code == 422, f"{why} -> 422", f"-> {r.status_code}")
            det = (r.json().get("detail") or {}) if r.status_code == 422 else {}
            check(k in (det.get("rejected") or {}), f"{why} é REJEITADO com motivo",
                  f"-> {(det.get('rejected') or {}).get(k)}")
            cur = jget(c, "/api/settings")
            check(cur.get(k) != bad[k], f"{why} não foi persistido",
                  f"-> {k} continua {cur.get(k)}")

        # ALL-OR-NOTHING: um campo válido junto de um inválido não entra sozinho.
        # Perfil de risco é combinação (capital × teto de vega × Kelly × DTE);
        # aplicar metade produziria um perfil que o usuário nunca revisou.
        before = jget(c, "/api/settings")
        r = c.post("/api/settings", json={"bankroll": -1, "target_dte": 60}, timeout=60)
        check(r.status_code == 422, "payload misto (1 válido + 1 inválido) -> 422",
              f"-> {r.status_code}")
        det = r.json().get("detail") or {}
        check("target_dte" in (det.get("aceitos_que_nao_foram_aplicados") or []),
              "a resposta DIZ quais campos válidos ficaram de fora",
              f"-> {det.get('aceitos_que_nao_foram_aplicados')}")
        after = jget(c, "/api/settings")
        check(after.get("target_dte") == before.get("target_dte"),
              "rejeição é ATÔMICA: target_dte válido NÃO foi aplicado",
              f"-> continua {after.get('target_dte')}")
        check(after.get("bankroll") == before.get("bankroll"),
              "bankroll preservado após tentativa inválida", f"-> {after.get('bankroll')}")
        # e um payload todo válido continua funcionando
        r = c.post("/api/settings", json={"target_dte": 60, "max_positions": 5}, timeout=60)
        check(r.status_code == 200 and r.json().get("ok") is True,
              "payload todo válido é aplicado", f"-> {r.status_code}")
        check(r.json().get("saved") == {"target_dte": 60.0, "max_positions": 5},
              "saved ecoa exatamente o que entrou", f"-> {r.json().get('saved')}")
        c.post("/api/settings", json={"target_dte": 45, "max_positions": 4}, timeout=60)

        # invalidação de cache: o handler tinha aqui um `for ...: pass` — não
        # invalidava nada, então trocar o universo continuava servindo a
        # superfície antiga até o TTL vencer.
        jget(c, "/api/radar")                     # popula chain:/candles:/dvol:
        primed = db.cache_get("chain:BTC")
        check(primed is not None, "radar populou o cache de chain:BTC")
        r = c.post("/api/settings", json={"min_oi": 1.5}, timeout=60)
        check(r.json().get("cache_cleared", 0) > 0,
              "mudar min_oi INVALIDA o cache de leitura", f"-> {r.json().get('cache_cleared')} linhas")
        check(db.cache_get("chain:BTC") is None, "chain:BTC foi de fato apagada")
        r = c.post("/api/settings", json={"display_currency": "BRL"}, timeout=60)
        check(r.json().get("cache_cleared", 0) == 0 or db.cache_get("chain:BTC") is None,
              "mudar só exibição não precisa invalidar dados")
        c.post("/api/settings", json={"display_currency": "USD", "min_oi": 0.5}, timeout=60)

        # ------------------------------------------------------------ dados
        print("\n[dados — modo demo esperado neste sandbox, sem egresso]")
        rad = jget(c, "/api/radar")
        reads = rad.get("reads") or []
        check(len(reads) == 2, "radar devolve BTC e ETH", f"-> {len(reads)}")
        for rd in reads:
            check(rd.get("ok") is True, f"radar {rd.get('ccy')} ok")
            check(rd.get("is_demo") is True, f"radar {rd.get('ccy')} ROTULA demo",
                  "(honestidade: nunca mostrar número sintético como real)")
            check((rd.get("sources") or {}).get("options") == "demo",
                  f"radar {rd.get('ccy')} expõe a fonte por camada",
                  f"-> {rd.get('sources')}")
            check(rd.get("spot") and rd.get("spot") > 0, f"radar {rd.get('ccy')} tem spot",
                  f"-> {rd.get('spot')}")
            check((rd.get("vrp") or {}).get("band") is not None,
                  f"radar {rd.get('ccy')} classifica a banda de VRP",
                  f"-> {(rd.get('vrp') or {}).get('band')}")
            check(rd.get("n_options", 0) > 50, f"radar {rd.get('ccy')} populou a cadeia",
                  f"-> {rd.get('n_options')}")
        check("Etapa 0" in rad.get("aviso", ""), "aviso de escopo presente no radar")
        check("recomendação de investimento" in rad.get("aviso", "").lower()
              or "não é recomendação" in rad.get("aviso", ""),
              "aviso mantém o DNA de honestidade do FutAnalytics")

        sup = jget(c, "/api/surface?ccy=BTC")
        check(sup.get("n", 0) > 50, "surface popula opções", f"-> n={sup.get('n')}")
        check(sup.get("source") == "demo", "surface rotula a fonte", f"-> {sup.get('source')}")
        hist = jget(c, "/api/history?ccy=BTC&days=90")
        check(len(hist.get("candles") or []) > 0, "history devolve velas",
              f"-> {len(hist.get('candles') or [])}")
        check("rv_series" in hist, "history traz a série de RV (base do backtest)")
        tp = jget(c, "/api/test-provider")
        check(isinstance(tp, dict) and len(tp) >= 4, "test-provider diagnostica cada provedor",
              f"-> {sorted(tp)}")
        # chaves com "_" são METADADOS (orçamento de tempo, disjuntores), não
        # sondagem de provedor — o invariante vale para as entradas de provedor.
        probes = {k: v for k, v in tp.items() if not k.startswith("_") and isinstance(v, dict)}
        check(len(probes) >= 4, "há sondagem para cada provedor da cadeia",
              f"-> {sorted(probes)}")
        check(all("error" in v or "ok" in v for v in probes.values()),
              "cada provedor reporta ok OU o erro real (nunca silêncio)")
        check(all(v.get("ms") is not None for v in probes.values()),
              "cada sondagem reporta o tempo gasto (para ver o teto na prática)",
              f"-> máx {max((v.get('ms') or 0) for v in probes.values())} ms")
        check("_budget" in tp and tp["_budget"].get("deadline_global_s"),
              "orçamento de tempo exposto na tela", f"-> {tp.get('_budget')}")
        check("_breakers" in tp, "estado dos disjuntores exposto na tela")

        # ------------------------------------------------------------ trades
        print("\n[trades — o ciclo que nasceu quebrado]")
        legs = [
            {"instrument": "BTC-30OCT26-76000-P", "kind": "P", "strike": 76000,
             "side": "buy", "qty": 1, "price": 0.0110, "iv": 47.24},
            {"instrument": "BTC-30OCT26-80000-P", "kind": "P", "strike": 80000,
             "side": "sell", "qty": 1, "price": 0.0210, "iv": 41.10},
            {"instrument": "BTC-30OCT26-88000-C", "kind": "C", "strike": 88000,
             "side": "sell", "qty": 1, "price": 0.0180, "iv": 38.90},
            {"instrument": "BTC-30OCT26-92000-C", "kind": "C", "strike": 92000,
             "side": "buy", "qty": 1, "price": 0.0095, "iv": 36.20},
        ]
        payload = {
            "ccy": "BTC", "structure": "iron_condor", "direction": "short_vol",
            "legs": legs, "entry_iv": 38.90, "premium": 0.0185, "notional": 1554.0,
            "contracts": 1, "vega": 3.2, "delta": -0.04, "max_loss_stress": 610.0,
            "prob_edge": 0.62, "ev": 184.0, "grade": "B", "check_score": 7.5,
            "expiry": "2026-10-30", "notes": "smoke test Etapa 0",
        }
        r = c.post("/api/trades", json=payload, timeout=60)
        check(r.status_code == 200, "POST /api/trades", f"-> {r.status_code} {r.text[:160]}")
        body = r.json() if r.status_code == 200 else {}
        tid = body.get("id")
        check(isinstance(tid, int) and tid > 0, "trade recebeu id inteiro", f"-> {tid}")
        t = body.get("trade") or {}
        check(len(t.get("legs") or []) == 4, "legs sobreviveram à ida e volta do SQLite",
              f"-> {len(t.get('legs') or [])} (JSON serializado/desserializado)")
        check(t.get("entry_iv") == 38.90, "entry_iv preservado", f"-> {t.get('entry_iv')}")
        check(t.get("status") == "open", "status default 'open'", f"-> {t.get('status')}")
        check(t.get("stake_pct") is not None,
              "stake_pct derivado de notional/bankroll", f"-> {t.get('stake_pct')}%")
        check(abs((t.get("stake_pct") or 0) - round(1554.0 / 2500 * 100, 3)) < 0.01,
              "stake_pct correto (1554/2500)", f"-> {t.get('stake_pct')}%")

        # moeda fora do escopo tem que ser recusada
        r = c.post("/api/trades", json={**payload, "ccy": "SOL"}, timeout=60)
        check(r.status_code == 400, "SOL recusado (escopo BTC+ETH)", f"-> {r.status_code}")
        r = c.post("/api/trades", json={**payload, "ccy": "DOGE"}, timeout=60)
        check(r.status_code == 400, "DOGE recusado", f"-> {r.status_code}")

        lst = jget(c, "/api/trades")
        check(lst["stats"]["total"] == 1 and lst["stats"]["open"] == 1,
              "listagem conta 1 trade aberto", f"-> {lst['stats']}")

        # CVL: vendi a 38,90 e o mercado fechou a 37,50 -> venci (vendi caro)
        r = c.post(f"/api/trades/{tid}/closing-iv", json={"closing_iv": 37.50}, timeout=60)
        check(r.status_code == 200, "POST closing-iv", f"-> {r.status_code} {r.text[:140]}")
        cvl = r.json().get("cvl") if r.status_code == 200 else None
        check(cvl is not None and abs(cvl - 1.4) < 1e-6,
              "CVL = entry_iv − closing_iv para quem VENDE vol", f"-> {cvl} (esperado 1.4)")
        r = c.post(f"/api/trades/{tid}/closing-iv", json={"closing_iv": 42.0}, timeout=60)
        cvl2 = r.json().get("cvl") if r.status_code == 200 else None
        check(cvl2 is not None and cvl2 < 0,
              "CVL negativo quando a IV sobe contra a posição", f"-> {cvl2}")

        # direção long_vol INVERTE o sinal do CVL
        r = c.post("/api/trades", json={**payload, "direction": "long_vol",
                                        "structure": "long_straddle"}, timeout=60)
        lid = r.json().get("id") if r.status_code == 200 else None
        if lid:
            r = c.post(f"/api/trades/{lid}/closing-iv", json={"closing_iv": 37.50}, timeout=60)
            lcvl = r.json().get("cvl") if r.status_code == 200 else None
            check(lcvl is not None and lcvl < 0,
                  "long_vol: IV caindo dá CVL NEGATIVO (sinal invertido)",
                  f"-> {lcvl} vs short_vol +1.4")
        else:
            check(False, "long_vol: trade criado para testar inversão de sinal")

        r = c.post(f"/api/trades/{tid}/settle",
                   json={"status": "expired", "pnl": 155.4, "notes": "expirou sem valor"},
                   timeout=60)
        check(r.status_code == 200, "POST settle", f"-> {r.status_code} {r.text[:140]}")
        st = (r.json().get("trade") or {}) if r.status_code == 200 else {}
        check(st.get("status") == "expired" and st.get("pnl") == 155.4,
              "settle gravou status e pnl", f"-> {st.get('status')} / {st.get('pnl')}")
        r = c.post(f"/api/trades/{tid}/settle", json={"status": "nonsense"}, timeout=60)
        check(r.status_code == 400, "status inválido rejeitado no settle", f"-> {r.status_code}")
        r = c.post("/api/trades/999999/settle", json={"status": "closed"}, timeout=60)
        check(r.status_code == 404, "settle em id inexistente -> 404", f"-> {r.status_code}")
        r = c.post("/api/trades/999999/closing-iv", json={"closing_iv": 30.0}, timeout=60)
        check(r.status_code == 404, "closing-iv em id inexistente -> 404", f"-> {r.status_code}")

        # portfolio: o trade liquidado não deve mais contar como aberto
        pf = jget(c, "/api/portfolio")
        check(pf.get("open_positions") == 1, "portfolio conta só posições abertas",
              f"-> {pf.get('open_positions')} (o expired saiu, o long_vol ficou)")
        check("agregação aritmética" in pf.get("aviso", ""),
              "portfolio AVISA que superestima diversificação (Etapa 4 pendente)")
        check(pf.get("vega_total", 0) > 0, "vega agregado", f"-> {pf.get('vega_total')}")

        lst = jget(c, "/api/trades")
        check(lst["stats"]["total"] == 2 and lst["stats"]["closed"] >= 0,
              "stats refletem 2 trades", f"-> {lst['stats']}")
        check(lst["stats"].get("cvl_mean") is not None,
              "cvl_mean agregado (o KPI herdado do CLV)", f"-> {lst['stats'].get('cvl_mean')}")

        # ------------------------------------------------------------ backup
        print("\n[backup — o único caminho de saída do SQLite efêmero]")
        bk = jget(c, "/api/backup")
        check(isinstance(bk, dict) and "trades" in bk, "export contém trades")
        check(len(bk.get("trades") or []) == 2, "export levou os 2 trades",
              f"-> {len(bk.get('trades') or [])}")
        r = c.delete(f"/api/trades/{lid}", timeout=60) if lid else None
        check(r is not None and r.status_code == 200, "DELETE trade",
              f"-> {r.status_code if r else 'n/a'}")
        r = c.delete(f"/api/trades/{lid}", timeout=60) if lid else None
        check(r is not None and r.status_code == 404, "DELETE duas vezes -> 404",
              f"-> {r.status_code if r else 'n/a'}")
        r = c.post("/api/backup", json=bk, timeout=60)
        check(r.status_code == 200 and r.json().get("ok"), "POST /api/backup (reimport)",
              f"-> {r.status_code}")
        lst2 = jget(c, "/api/trades")
        check(lst2["stats"]["total"] >= 2, "reimport restaurou o histórico",
              f"-> total {lst2['stats']['total']}")

        # ------------------------------------------------------------ estático
        print("\n[estático]")
        r = c.get("/", timeout=60)
        check(r.status_code == 200 and "SigmaDesk" in r.text, "GET / serve o painel",
              f"-> {r.status_code}, {len(r.text)} bytes")
        r = c.head("/", timeout=60)
        check(r.status_code == 200, "HEAD / (health check do Render usa HEAD)",
              f"-> {r.status_code}")
        r = c.get("/api/inexistente", timeout=60)
        check(r.status_code == 404, "rota inexistente -> 404 limpo", f"-> {r.status_code}")

    print("\n" + "=" * 74)
    print(f"RESULTADO: {len(oks)} verificações OK · {len(fails)} FALHAS")
    print("=" * 74)
    if fails:
        print("\nFALHAS:")
        for f in fails:
            print("  ✗", f)
    else:
        print("\nTodos os endpoints respondem e o ciclo de trade fecha de ponta a ponta.")
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
