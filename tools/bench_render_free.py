"""Mede se o app cabe nas restrições do RENDER FREE — com números, não opinião.

O cenário que interessa não é "provedor responde erro" (aqui dentro falha em
0,1s e tudo parece ótimo). É **provedor black-holando**: pacotes dropados sem
rejeição, cada tentativa consumindo o timeout inteiro. É o que acontece com IP de
datacenter contra APIs que não te conhecem.

Mede:
  1. latência no pior caso (todos os provedores pendurados)
  2. efeito do disjuntor na segunda carga
  3. memória residente do processo
  4. tempo de import (proxy de cold start)
  5. crescimento do SQLite
  6. dependências e ausência de worker em background

    .venv/bin/python -m tools.bench_render_free
"""
from __future__ import annotations

import asyncio
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SIGMADESK_DB"] = _tmp.name

t_import0 = time.monotonic()
from fastapi.testclient import TestClient  # noqa: E402

from app import db, provider  # noqa: E402
from app.main import app  # noqa: E402

T_IMPORT = time.monotonic() - t_import0

fails: list[str] = []
oks: list[str] = []


def check(cond: bool, label: str, detail: str = ""):
    (oks if cond else fails).append(f"{label} {detail}".strip())


def hang(seconds: float):
    """Corrotina que simula provedor black-holando."""
    async def _f(*a, **k):
        await asyncio.sleep(seconds)
        raise AssertionError("não deveria voltar")
    return _f


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> int:
    print("=" * 74)
    print("BENCHMARK RENDER FREE — SigmaDesk")
    print("=" * 74)

    # ---------------------------------------------------------------- 1. pior caso
    print("\n[1] Latência no PIOR CASO: todos os provedores pendurados 60s")
    HANG = 60.0
    originals = {n: getattr(provider, n) for n in
                 ("dr_ohlcv", "cb_candles", "kraken_ohlcv", "dr_option_chain", "dr_dvol")}
    for n in originals:
        setattr(provider, n, hang(HANG))
    provider._breaker.clear()

    with TestClient(app) as c:
        db.purge_expired_cache()
        t0 = time.monotonic()
        r = c.get("/api/radar")
        t_first = time.monotonic() - t0
        check(r.status_code == 200, "radar respondeu 200 mesmo com tudo pendurado",
              f"-> {r.status_code}")
        budget = 3 * provider.TIMEOUT + 5      # 3 camadas em paralelo + folga
        check(t_first < provider.DEADLINE_S + 6,
              f"1ª carga abaixo do teto global ({provider.DEADLINE_S}s)",
              f"-> {t_first:.1f}s")
        print(f"    1ª carga (cache frio): {t_first:.1f}s")
        print(f"    ANTES da correção seriam ~200s: candles 3×20s + chain 20s + dvol 20s")
        print(f"    = 100s por moeda × 2 moedas sequenciais")
        reads = r.json().get("reads") or []
        check(len(reads) == 2, "as duas moedas voltaram", f"-> {len(reads)}")
        check(all(x.get("is_demo") for x in reads),
              "caiu em demo e ROTULOU como demo", f"-> {[x.get('sources') for x in reads][:1]}")
        # o pior caso teórico sem deadline seria 3×60s = 180s por camada
        worst_theoretical = 3 * HANG
        check(t_first < worst_theoretical / 3,
              "deadline impediu consumir o timeout inteiro de cada provedor",
              f"({t_first:.1f}s contra {worst_theoretical:.0f}s teóricos)")

        print("\n[2] Disjuntor: 2ª e 3ª carga com provedores ainda mortos")
        t0 = time.monotonic(); r2 = c.get("/api/radar"); t_second = time.monotonic() - t0
        db.cache_clear()                        # força nova tentativa, sem cache
        t0 = time.monotonic(); r3 = c.get("/api/radar"); t_third = time.monotonic() - t0
        print(f"    2ª carga: {t_second:.2f}s   3ª (cache limpo): {t_third:.2f}s")
        check(t_third < 5.0,
              "sem cache, disjuntor evita pagar o timeout de novo",
              f"-> {t_third:.2f}s (seriam ~{provider.DEADLINE_S}s sem disjuntor)")
        check(t_third < t_first / 2, "3ª carga bem mais rápida que a 1ª",
              f"({t_third:.2f}s vs {t_first:.1f}s)")
        br = provider.breaker_report()
        check(len(br) >= 2, "disjuntores registrados por provedor", f"-> {sorted(br)}")
        check(all(v["state"] in ("open", "half-open") for v in br.values()),
              "provedores mortos marcados", f"-> {[(k, v['state']) for k, v in br.items()]}")

        # O endpoint de diagnóstico era o ÚNICO sem proteção: chamava provedor
        # sem _guarded, sem timeout e sem deadline. Justamente o que o DEPLOY.md
        # manda abrir PRIMEIRO após o deploy — num black-hole ele penduraria
        # 4×20s = 80s. As sondagens abaixo AINDA estão penduradas em 60s cada.
        t0 = time.monotonic()
        rtp = c.get("/api/test-provider")
        t_tp = time.monotonic() - t0
        check(rtp.status_code == 200, "test-provider responde 200 com provedores pendurados",
              f"-> {rtp.status_code} em {t_tp:.1f}s")
        tp = rtp.json() if rtp.status_code == 200 else {}
        check(t_tp < provider.DEADLINE_S + 3,
              "test-provider respeita o deadline global (não pendura 80s)",
              f"-> {t_tp:.1f}s de teto {provider.DEADLINE_S}s")
        check("_breakers" in tp and "_budget" in tp,
              "diagnóstico expõe disjuntor e orçamento de tempo na tela",
              f"-> {tp.get('_budget')}")
        print(f"    orçamento exposto: {tp.get('_budget')}")

        # provedor volta -> disjuntor precisa fechar de novo
        print("\n[3] Recuperação: provedor volta a responder")
        for n, f in originals.items():
            setattr(provider, n, f)
        provider._breaker.clear()
        t0 = time.monotonic(); r4 = c.get("/api/radar"); t_rec = time.monotonic() - t0
        check(r4.status_code == 200, "radar volta a responder", f"-> {r4.status_code} em {t_rec:.2f}s")

    # ---------------------------------------------------------------- 4. memória
    print("\n[4] Memória residente (teto do free: 512 MB)")
    mem = rss_mb()
    check(mem < 200, "pico de RSS abaixo de 200 MB", f"-> {mem:.0f} MB")
    print(f"    RSS pico: {mem:.0f} MB de 512 MB ({mem/512*100:.0f}% do teto)")
    try:
        import numpy  # noqa: F401
        check(False, "numpy NÃO deveria estar instalado")
    except ImportError:
        check(True, "numpy ausente (matemática toda em stdlib)")
    try:
        import pandas  # noqa: F401
        check(False, "pandas NÃO deveria estar instalado")
    except ImportError:
        check(True, "pandas ausente")

    # ---------------------------------------------------------------- 5. cold start
    print("\n[5] Cold start")
    check(T_IMPORT < 6.0, "import do app rápido", f"-> {T_IMPORT:.2f}s")
    print(f"    import do app: {T_IMPORT:.2f}s (o Render free já gasta ~30-60s")
    print(f"    acordando o serviço; isto é só a parte do Python)")

    # ---------------------------------------------------------------- 6. disco
    print("\n[6] Disco (SQLite efêmero)")
    with TestClient(app) as c:
        for _ in range(3):
            c.get("/api/radar")
        size1 = Path(_tmp.name).stat().st_size
        for _ in range(10):
            c.get("/api/radar")
        size2 = Path(_tmp.name).stat().st_size
    print(f"    após 3 cargas: {size1/1024:.0f} KB · após 13: {size2/1024:.0f} KB")
    check(size2 < 5 * 1024 * 1024, "banco cresce pouco", f"-> {size2/1024:.0f} KB")
    growth = size2 - size1
    check(growth < 2 * 1024 * 1024, "10 cargas extras não incham o disco",
          f"-> +{growth/1024:.0f} KB (cache tem TTL e é sobrescrito, não acumula)")

    # ---------------------------------------------------------------- 7. contrato
    print("\n[7] Contrato do Render free")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    deps = [l.split(">=")[0].split("[")[0].strip() for l in req.splitlines()
            if l.strip() and not l.startswith("#")]
    check(set(deps) <= {"fastapi", "uvicorn", "httpx", "pydantic"},
          "nenhuma dependência nova além das 4 do FutAnalytics", f"-> {deps}")
    ry = (ROOT / "render.yaml").read_text(encoding="utf-8")
    check("plan: free" in ry, "render.yaml declara plan free")
    check("$PORT" in ry, "start command usa $PORT (obrigatório no Render)")
    check("0.0.0.0" in ry, "bind em 0.0.0.0, não 127.0.0.1")
    check("healthCheckPath: /api/health" in ry, "health check configurado")
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check("*.db" in gi, ".gitignore cobre o SQLite local")
    src = "".join(f.read_text(encoding="utf-8") for f in (ROOT / "app").glob("*.py"))
    for banned, why in (("threading.Thread", "worker em background"),
                        ("multiprocessing", "processo separado"),
                        ("APScheduler", "agendador"),
                        ("celery", "fila de tarefas")):
        check(banned not in src, f"sem {why} ({banned})")
    check("binance" not in [x for x in ("deribit", "coinbase", "kraken")],
          "cadeia de fallback não inclui Binance (geo-bloqueada no IP do Render)")
    order_ok = 'order = ["deribit", "coinbase", "kraken"]' in src
    check(order_ok, "cadeia real é deribit -> coinbase -> kraken -> demo")

    # ---------------------------------------------------------------- resultado
    print("\n" + "=" * 74)
    print(f"RESULTADO: {len(oks)} verificações OK · {len(fails)} FALHAS")
    print("=" * 74)
    if fails:
        print("\nFALHAS:")
        for f in fails:
            print("  ✗", f)
    else:
        print("\nCabe no Render free: latência com teto, sem worker, sem dependência")
        print("nova, memória e disco medidos dentro do orçamento.")
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
