"""Validação da Etapa 0 contra DADOS REAIS capturados das APIs públicas.

Roda offline (usa app/fixtures_real.json) e é determinístico. Serve para duas
coisas: provar que o motor produz números sanos sobre dado real, e funcionar
como teste de regressão das etapas seguintes.

    .venv/bin/python -m tools.validate_etapa0
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import provider, volread

FIX = json.loads((Path(__file__).resolve().parent.parent / "app" / "fixtures_real.json")
                 .read_text(encoding="utf-8"))

fails: list[str] = []
oks: list[str] = []


def check(cond: bool, label: str, detail: str = ""):
    if cond:
        oks.append(f"{label} {detail}".strip())
    else:
        fails.append(f"{label} {detail}".strip())


def norm_options() -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    out = [o for o in (provider._option_row(r, now) for r in FIX["options_btc_sample"]) if o]
    return sorted(out, key=lambda o: (o["expiry"], o["strike"], o["kind"]))


def main() -> int:
    print("=" * 74)
    print("VALIDAÇÃO ETAPA 0 — SigmaDesk · dados reais de 26/set/2026")
    print("=" * 74)

    # ---------------------------------------------------------------- 1. parser
    print("\n[1] Parser de instrumento sobre nomes REAIS da Deribit")
    opts = norm_options()
    check(len(opts) == len(FIX["options_btc_sample"]),
          f"todas as {len(FIX['options_btc_sample'])} linhas reais parsearam",
          f"-> {len(opts)}")
    by_name = {o["instrument"]: o for o in opts}
    a = by_name.get("BTC-28SEP26-83000-P") or {}
    check(a.get("strike") == 83000 and a.get("kind") == "P"
          and a.get("expiry") == "2026-09-28", "BTC-28SEP26-83000-P decomposto")
    check(abs((a.get("mark_iv") or 0) - 18.69) < 1e-9, "mark_iv preservado em %", f"({a.get('mark_iv')})")
    check(a.get("liquid") is True, "83000-P é ponta líquida (spread 22%, OI 212)")

    # bid nulo = ponta morta. Caso REAL capturado, não bug.
    b = by_name.get("BTC-28SEP26-87500-C") or {}
    check(b.get("bid") is None and b.get("liquid") is False,
          "bid_price=null -> marcado NÃO líquido (caso real da cadeia)")
    # OI zero = sem posição aberta
    c = by_name.get("BTC-28SEP26-88000-P") or {}
    check(c.get("oi") == 0.0 and c.get("liquid") is False,
          "OI=0 -> não líquido mesmo com bid/ask vivos")
    d = by_name.get("BTC-30OCT26-110000-C") or {}
    check(d.get("liquid") is True and (d.get("oi") or 0) == 1195.2,
          "30OCT26-110000-C líquido (OI 1195, spread 9,5%)")
    n_liq = sum(1 for o in opts if o["liquid"])
    print(f"    {len(opts)} instrumentos reais · {n_liq} com ponta negociável")

    # REGRESSÃO do bug pego por dado real: dia de vencimento SEM zero à esquerda
    for code, want in (("28SEP26", (2026, 9, 28)), ("9OCT26", (2026, 10, 9)),
                       ("3OCT26", (2026, 10, 3)), ("1AUG26", (2026, 8, 1)),
                       ("26MAR27", (2027, 3, 26)), ("30OCT26", (2026, 10, 30))):
        got = provider.parse_expiry(code)
        check(got == dt.date(*want), f"parse_expiry('{code}')", f"-> {got}")
    check(provider.parse_expiry("31FEB26") is None, "data impossível rejeitada (31FEB26)")
    check(provider.parse_expiry("28XYZ26") is None, "mês inválido rejeitado (28XYZ26)")
    check(provider.parse_instrument("BTC-9OCT26-76000-P") is not None,
          "BTC-9OCT26-76000-P parseia (era o caso que falhava)")
    check(provider.parse_instrument("BTC-USDC-9OCT26-76000-P") is not None,
          "variante linear com dia de 1 dígito também parseia")

    # ---------------------------------------------------------------- 2. RV real
    print("\n[2] Volatilidade realizada sobre 30 velas REAIS da Coinbase")
    candles = FIX["candles_btc"]
    rv = volread.realized_vol(candles)
    check(rv.get("ok") is True, "cálculo convergiu")
    check(abs(rv["last_close"] - 84003.06) < 1e-6,
          "último close bate com a Coinbase", f"({rv['last_close']})")
    check(abs(rv["last_close"] - FIX["deribit_estimated_delivery_price"]) < 1.0,
          "cross-check Coinbase × Deribit < US$1", 
          f"({rv['last_close']} vs {FIX['deribit_estimated_delivery_price']})")
    for k in ("rv", "rv_long", "rv_short", "rv_parkinson", "rv_garman_klass", "rv_yang_zhang"):
        v = rv.get(k)
        check(v is not None and 5.0 < v < 300.0, f"{k} em faixa plausível", f"= {v}%")
    check(rv["range_used"] is True, "estimador de amplitude ativado (o 'xG' da vol)")
    # amplitude deve diferir de close-to-close: é justamente o ponto
    check(abs(rv["rv_yang_zhang"] - rv["rv_cc"]) > 0.01,
          "Yang-Zhang difere de close-to-close",
          f"({rv['rv_yang_zhang']}% vs {rv['rv_cc']}%)")
    check(rv["n_eff"] > 0, "amostra efetiva positiva", f"= {rv['n_eff']}")
    check(rv["jumps"]["diffusion_vol"] is not None, "bipower separou difusão de salto",
          f"(difusão {rv['jumps']['diffusion_vol']}%, salto {rv['jumps'].get('jump_vol')}%)")
    print(f"    RV blend {rv['rv']}% · longo {rv['rv_long']}% · curto {rv['rv_short']}%")
    print(f"    Parkinson {rv['rv_parkinson']}% · GK {rv['rv_garman_klass']}% · YZ {rv['rv_yang_zhang']}%")
    print(f"    skew {rv['skew']} · curtose excedente {rv['excess_kurtosis']}")

    # ---------------------------------------------------------------- 3. IV real
    print("\n[3] Estrutura a termo sobre IV REAL da Deribit")
    ts = volread.term_structure(opts)
    check(len(ts) >= 4, "vencimentos suficientes para term structure", f"-> {len(ts)}")
    ivs = [t["atm_iv"] for t in ts]
    check(all(5 < v < 200 for v in ivs), "todas as IV em faixa plausível",
          f"(min {min(ivs)}, max {max(ivs)})")
    fwds = [t["forward"] for t in ts]
    check(fwds == sorted(fwds), "curva de forward em contango (real: 84.022→88.318)",
          f"-> {fwds[0]}…{fwds[-1]}")
    for t in ts[:6]:
        print(f"    {t['expiry']}  DTE {t['dte']:6.1f}  IV {t['atm_iv']:6.2f}%  "
              f"fwd {t['forward']:9.0f}  líq {t['n_liquid']}/{t['n_all']}")
    iv30 = volread.interpolate_iv(ts, 30.0)
    check(iv30 is not None and 5 < iv30 < 200, "interpolação de IV em 30d", f"= {iv30:.2f}%")
    # interpolação em variância total deve ficar ENTRE os vizinhos
    lo = max([t for t in ts if t["dte"] <= 30], key=lambda t: t["dte"], default=None)
    hi = min([t for t in ts if t["dte"] >= 30], key=lambda t: t["dte"], default=None)
    if lo and hi and lo is not hi:
        check(min(lo["atm_iv"], hi["atm_iv"]) - 1 <= iv30 <= max(lo["atm_iv"], hi["atm_iv"]) + 1,
              "IV 30d entre os vizinhos de vencimento",
              f"({lo['atm_iv']} ≤ {iv30:.2f} ≤ {hi['atm_iv']})")
    fw = volread.forward_variance(ts)
    check(len(fw) >= 2, "forward variance calculada", f"-> {len(fw)} pares")
    for f in fw[:4]:
        print(f"    {f['from']} → {f['to']}: fwd vol {f['fwd_vol']}% ({f['regime']})")

    # ---------------------------------------------------------------- 4. model-free
    print("\n[4] Variância model-free (fórmula VIX) — amostra parcial")
    mf = volread.model_free_variance(opts, ts[0]["expiry"]) if ts else None
    check(mf is None or mf["implied_vol"] > 0,
          "degrada com honestidade quando a amostra é parcial",
          ("-> None (esperado: 17 instrumentos não cobrem uma cadeia)" if mf is None
           else f"-> {mf['implied_vol']}%"))
    print(f"    resultado: {'None (amostra insuficiente — comportamento correto)' if mf is None else mf['implied_vol']}")

    # ---------------------------------------------------------------- 5. skew
    print("\n[5] Skew e posicionamento")
    best = max(ts, key=lambda t: t["n_liquid"]) if ts else None
    sk = volread.skew_25d(opts, best["expiry"]) if best else None
    check(sk is None or "put_skew" in sk,
          "skew degrada para None quando não há asas negociáveis",
          ("-> None (só 3 opções no vencimento mais líquido; asa precisa de OI)"
           if sk is None else f"-> put_skew {sk.get('put_skew')}"))
    pcr = volread.put_call_oi(opts)
    check(pcr["put_oi"] > 0 and pcr["call_oi"] > 0, "OI de puts e calls agregado",
          f"(P {pcr['put_oi']} / C {pcr['call_oi']}, razão {pcr['ratio']})")
    check(pcr["lean"] is not None, "leitura de posicionamento", f"-> {pcr['lean']}")

    # ---------------------------------------------------------------- 6. DVOL real
    print("\n[6] Reversão de vol sobre séries REAIS de DVOL")
    # 6a — série ATUAL (jun–set/2026), que sobrepõe as velas da Coinbase
    dvol = [{"ts": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]}
            for r in FIX["dvol_btc_2026_ohlcv"]]
    dcur = FIX["dvol_btc_current"]
    check(dvol[-1]["ts"] // 1000 >= 1790380800, "série DVOL chega até a data da captura")
    check(abs(dvol[-1]["c"] - dcur["value"]) < 1e-9, "último DVOL confere com o oficial",
          f"= {dcur['value']}% em {dcur['date']}")
    ou = volread.ou_forecast([d["c"] for d in dvol], 30.0)
    check(ou.get("ok") is True, "Ornstein-Uhlenbeck estimado sobre DVOL 2026")
    check(ou["kappa_daily"] >= 0, "κ não negativo (reversão, não explosão)", f"= {ou['kappa_daily']}")
    check(30 < ou["sigma_bar"] < 60, "σ̄ dentro da faixa observada jun–set/2026 (33,6–49,4)",
          f"= {ou['sigma_bar']}%")
    check(ou["rv_forecast"] > 0, "previsão prospectiva produzida",
          f"= {ou['rv_forecast']}% (atual {ou['rv_now']}%, meia-vida {ou['half_life_days']}d)")
    print(f"    2026: σ̄ {ou['sigma_bar']}% · κ {ou['kappa_daily']}/dia · "
          f"meia-vida {ou['half_life_days']}d · previsão 30d {ou['rv_forecast']}%")

    # 6b — janela 2024 (vol alta): o MESMO código tem que se adaptar ao regime
    w = FIX["dvol_btc_2024_window"]
    ou24 = volread.ou_forecast(w["close"], 30.0)
    check(ou24.get("ok") is True, "OU estimado na janela de 2024")
    check(ou24["sigma_bar"] > ou["sigma_bar"] + 5,
          "σ̄ de 2024 > σ̄ de 2026: o motor LÊ o regime, não decora um número",
          f"({ou24['sigma_bar']}% vs {ou['sigma_bar']}%)")
    check(30 < ou24["sigma_bar"] < 90, "σ̄ 2024 dentro da faixa observada (41,5–83,0)",
          f"= {ou24['sigma_bar']}%")
    print(f"    2024: σ̄ {ou24['sigma_bar']}% · κ {ou24['kappa_daily']}/dia · "
          f"meia-vida {ou24['half_life_days']}d (regime de vol alta)")

    # 6c — CROSS-CHECK DECISIVO: dois métodos independentes de medir IV
    check(abs(dcur["value"] - iv30) < 3.0,
          "DVOL oficial ≈ IV 30d interpolado da cadeia (métodos independentes)",
          f"({dcur['value']}% vs {iv30:.2f}%, Δ {abs(dcur['value'] - iv30):.2f} pt)")
    print(f"    DVOL oficial {dcur['value']}% × IV interpolado {iv30:.2f}% "
          f"→ Δ {abs(dcur['value'] - iv30):.2f} pt")

    # ---------------------------------------------------------------- 7. VRP
    print("\n[7] VRP — o sinal mestre")
    # A previsão PROSPECTIVA tem que vir de uma série de RV, NÃO da série de IV.
    # Misturar as duas é o erro que inverte o sinal: foi o que a primeira versão
    # deste teste fez, e produziu um VRP de −22 pts sem sentido.
    demo_c = provider.demo_candles("BTC", 400)
    rv_series = []
    for i in range(90, len(demo_c) + 1):
        r = volread.realized_vol(demo_c[max(0, i - 90):i])
        if r.get("ok") and r.get("rv"):
            rv_series.append(r["rv"])
    ou_rv = volread.ou_forecast(rv_series, 30.0)
    check(ou_rv.get("ok") is True and len(rv_series) > 100,
          "OU ajustado sobre série de RV (não de IV)", f"n={len(rv_series)}")
    fc = ou_rv.get("rv_forecast")
    # O z-score de VRP precisa de histórico. Com 30 velas reais dá para formar UM
    # único ponto de RV, então o histórico real é curto por construção do fixture
    # — o provider ao vivo entrega 400+ velas e resolve isso sozinho. Aqui o
    # histórico é SINTÉTICO e serve só para exercitar a mecânica do z-score.
    hist = [8.0] * 60 + [4.0, 12.0, 6.0, 9.0, 2.0, 11.0, 5.0, 7.0, 10.0, 3.0]
    v = volread.vrp_read(iv30, rv["rv"], fc, hist)
    check(v.get("ok") is True, "VRP calculado")
    check(v["vrp"] == round(iv30 - fc, 2),
          "VRP usa a previsão PROSPECTIVA, não a RV trailing",
          f"({iv30:.2f} − {fc} = {v['vrp']})")
    check(v["band"] in ("muito rico", "saudável", "magro", "marginal", "NEGATIVO"),
          "classificação de banda", f"-> {v['band']}")
    check(bool(v["action"]), "recomendação acionável emitida")
    print(f"    IV 30d {v['iv_30d']}% − RV prevista {v['rv_forecast']}% = VRP {v['vrp']} pts")
    print(f"    banda: {v['band']} → {v['action']}")
    print(f"    razão IV/RV {v['ratio']} · VRP trailing {v['vrp_trailing']} pts")
    neg = volread.vrp_read(iv30, rv["rv"], iv30 + 5.0, None)
    check(neg["band"] == "NEGATIVO" and "não venda" in neg["action"],
          "VRP negativo bloqueia venda de prêmio (regra de sobrevivência)")

    # ---------------------------------------------------------------- 8. leitura full
    print("\n[8] Leitura completa empacotada (o que /api/radar devolve)")
    full = volread.read_surface(opts, candles, dvol, "BTC")
    check(full["ccy"] == "BTC", "moeda")
    check(full["n_options"] == len(opts), "contagem de opções", f"= {full['n_options']}")
    check("vrp" in full and "realized" in full and "term_structure" in full,
          "todas as camadas presentes")
    check(full.get("dvol_forecast", {}).get("ok") is True,
          "previsão de DVOL embutida como contexto (não substitui a de RV)")
    vh = full.get("vrp_history") or []
    check(isinstance(vh, list), "histórico de VRP por dia construído",
          f"-> {len(vh)} pontos (curto: fixture tem só 30 velas reais)")
    print(f"    ok={full['ok']} · spot {full['spot']} · IV30 {full['iv_30d']}% · "
          f"RV {full['realized']['rv']}% · VRP {full['vrp'].get('vrp')} pts")
    # a leitura REAL de 26/set/2026 tem que ser negativa — e o motor tem que recusar
    real_vrp = dcur["value"] - rv["rv"]
    check(real_vrp < 0, "VRP real de 26/set/2026 é NEGATIVO (IV abaixo da RV)",
          f"({dcur['value']} − {rv['rv']} = {real_vrp:.2f} pts)")
    vr = volread.vrp_read(iv30, rv["rv"], None, None)
    check(vr["band"] == "NEGATIVO" and "não venda" in vr["action"],
          "motor RECUSA vender prêmio nesse mercado — disciplina funcionando",
          f"-> {vr['action']}")

    # ---------------------------------------------------------------- 9. demo
    print("\n[9] Modo demo (fallback quando a rede falha) — ancorado em valor real")
    dc = provider.demo_candles("BTC", 400)
    check(abs(dc[-1]["c"] - FIX["candles_btc"][-1]["c"]) < 1e-6,
          "demo ancora no último close REAL da Coinbase", f"({dc[-1]['c']})")
    check(all(x["h"] >= max(x["o"], x["c"]) and x["l"] <= min(x["o"], x["c"]) for x in dc),
          "OHLC coerente em 400 velas (high ≥ max, low ≤ min)")
    dch = provider.demo_chain("BTC")
    check(len(dch) > 100, "cadeia demo populada", f"-> {len(dch)} instrumentos")
    check(all(provider.parse_instrument(o["instrument"]) for o in dch),
          "nomes demo passam pelo MESMO parser dos reais")
    dsm = [o for o in dch if o["liquid"]]
    check(len(dsm) > 20, "demo tem pontas líquidas", f"-> {len(dsm)}")
    dts = volread.term_structure(dch)
    check(len(dts) >= 4, "demo produz term structure", f"-> {len(dts)} vencimentos")
    # sorriso: asas devem custar mais que o ATM
    atm = min(dts, key=lambda t: abs(t["dte"] - 30))["atm_iv"] if dts else None
    wing = max((o["mark_iv"] for o in dch if o["liquid"]), default=None)
    check(atm and wing and wing > atm, "demo reproduz sorriso (asa > ATM)",
          f"(ATM {atm}%, asa {wing}%)")
    drv = volread.realized_vol(dc)
    check(drv["ok"] and 15 < drv["rv"] < 120, "RV do demo em faixa plausível", f"= {drv['rv']}%")

    # REGRESSÃO DA DESCALIBRAÇÃO: o demo precisa reproduzir as âncoras REAIS.
    # Antes ele ancorava DVOL em 19,0 e IV ATM em 19,0 (número errado meu, nunca
    # confrontado com o índice oficial de 34,88) e gerava RV de 47,5% — ou seja,
    # um VRP de −24 pts que não existe em mercado nenhum. Teste sobre demo não
    # pega isso; só teste do demo CONTRA o real pega.
    demo_last_dvol = provider.demo_dvol("BTC")[-1]["c"]
    check(abs(demo_last_dvol - dcur["value"]) < 8.0,
          "DVOL do demo ≈ DVOL oficial real", f"({demo_last_dvol} vs {dcur['value']})")
    dtsm = volread.term_structure(dch)
    demo_iv30 = volread.interpolate_iv(dtsm, 30.0) if dtsm else None
    check(demo_iv30 is not None and abs(demo_iv30 - iv30) < 5.0,
          "IV 30d do demo ≈ IV 30d interpolada do real", f"({demo_iv30:.2f} vs {iv30:.2f})")
    check(abs(drv["rv"] - rv["rv"]) < 7.0,
          "RV do demo ≈ RV realizada real", f"({drv['rv']} vs {rv['rv']})")
    demo_vrp = demo_iv30 - drv["rv"] if demo_iv30 else None
    real_vrp2 = iv30 - rv["rv"]
    check(demo_vrp is not None and abs(demo_vrp - real_vrp2) < 8.0,
          "VRP do demo ≈ VRP real (mesmo regime, não outra realidade)",
          f"({demo_vrp:.2f} vs {real_vrp2:.2f} pts)")
    # Exigir que o demo reproduza o SINAL de um VRP real de −3,8 pts seria
    # overfitting: esse número é pequeno perto do ruído de estimação de um único
    # dia. O invariante que importa é outro — o demo NUNCA pode pintar um VRP
    # "rico"/"saudável" (que convida a vender prêmio) quando o mercado real está
    # dizendo o contrário. Errar o tamanho é tolerável; errar a direção do
    # incentivo, não.
    demo_band = volread.vrp_read(demo_iv30, drv["rv"], None, None)["band"]
    real_band = volread.vrp_read(iv30, rv["rv"], None, None)["band"]
    check(demo_band not in ("muito rico", "saudável") or real_band in ("muito rico", "saudável"),
          "demo nunca finge VRP rico quando o real não é (invariante de incentivo)",
          f"-> demo '{demo_band}' / real '{real_band}'")
    # nomes do demo na MESMA convenião da exchange: dia sem zero à esquerda
    codes = sorted({o["instrument"].split("-")[1] for o in dch})
    single = [c for c in codes if not c[0].isdigit() or not c[1].isdigit()]
    check(bool(single), "demo emite vencimento com dia de 1 dígito (como a Deribit)",
          f"-> {single}")
    check(all(provider.parse_expiry(c) is not None for c in codes),
          "todos os códigos do demo parseiam", f"-> {codes}")
    check(all(re.fullmatch(r"[1-9]\d?[A-Z]{3}\d{2}", c) for c in codes),
          "códigos seguem {dia sem pad}{MÊS}{ano}", f"-> {codes}")
    print(f"    demo vs real: DVOL {demo_last_dvol}/{dcur['value']} · "
          f"IV30 {demo_iv30:.1f}/{iv30:.1f} · RV {drv['rv']}/{rv['rv']} · "
          f"VRP {demo_vrp:.1f}/{real_vrp2:.1f}")

    # REGRESSÃO DO PYTHONHASHSEED: o demo tem que ser idêntico em processos
    # diferentes. Este teste SÓ funciona como subprocesso — dentro de um processo
    # único a seed já está fixa e o bug não aparece. Foi assim que ele passou
    # despercebido: `hash("BTC")` é salgado por processo em CPython.
    probe = ("from app import provider;"
             "d=provider.demo_candles('BTC',60);c=provider.demo_chain('BTC');"
             "print(repr(d[-30]['c'])+'|'+c[0]['instrument']+'|'+c[-1]['instrument'])")
    outs = []
    for hs in ("0", "1", "12345"):
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent.parent),
                           env={**os.environ, "PYTHONHASHSEED": hs})
        outs.append(r.stdout.strip() or f"ERRO: {r.stderr.strip()[:120]}")
    check(len(set(outs)) == 1, "demo IDÊNTICO sob PYTHONHASHSEED 0/1/12345",
          f"-> {outs[0][:60]}")
    if len(set(outs)) > 1:
        print("      observado:", outs)
    # varredura por AST: procura CHAMADA real a hash(), ignorando docstring e
    # comentário (a checagem por substring dava falso positivo na própria
    # docstring que explica o bug).
    import ast
    tree = ast.parse(Path(__file__).resolve().parent.parent.joinpath(
        "app/provider.py").read_text(encoding="utf-8"))
    calls = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "hash"]
    check(not calls, "nenhuma chamada viva a hash() no provider",
          f"(linhas {calls})" if calls else "-> seed determinística via demo_seed()")

    # ---------------------------------------------------------------- resultado
    print("\n" + "=" * 74)
    print(f"RESULTADO: {len(oks)} verificações OK · {len(fails)} FALHAS")
    print("=" * 74)
    if fails:
        print("\nFALHAS:")
        for f in fails:
            print("  ✗", f)
        return 1
    print("\nTodas as verificações passaram sobre dados REAIS.")
    print("Camada de dados e leitura de volatilidade validadas — Etapa 0 concluída.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
