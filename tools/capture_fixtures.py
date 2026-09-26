"""Recaptura app/fixtures_real.json com dados REAIS das APIs públicas.

Os fixtures que validaram a Etapa 0 foram capturados em 26/set/2026. Eles
envelhecem: o DVOL muda de regime, a cadeia de opções expira, o spot anda. Este
script renova tudo e reescreve o arquivo, preservando a procedência.

RODE FORA DESTE SANDBOX. Aqui dentro não há egresso para Deribit/Coinbase
(TLS encerrado pelo proxy) — só `fetch_page` alcança. Na sua máquina, ou no
Render, o httpx chega normalmente.

    python -m tools.capture_fixtures            # captura e regrava
    python -m tools.capture_fixtures --dry-run  # captura e só relata
    python -m tools.capture_fixtures --check    # compara com o fixture existente

Depois de recapturar, rode a validação:

    python -m tools.validate_etapa0

Se alguma âncora mudar muito, os testes de calibração do modo demo vão falhar de
propósito — é o sinal de que `demo_dvol`/`demo_chain`/`demo_candles` precisam ser
reancorados no novo valor real. Isso é intencional: um demo descalibrado mente
melhor do que um demo ausente.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "app" / "fixtures_real.json"
DERIBIT = "https://www.deribit.com/api/v2/public"
COINBASE = "https://api.exchange.coinbase.com"
# Header HTTP é ASCII (latin-1 na prática): a primeira versão trazia
# "redistribuição" aqui e morria com UnicodeEncodeError ANTES de emitir
# qualquer request — falha que se disfarçava de "rede fora do ar".
UA = {"User-Agent": "SigmaDesk/0.1 (fixtures capture; personal use, no redistribution)"}


def _rpc(client: httpx.Client, method: str, **params):
    r = client.get(f"{DERIBIT}/{method}", params=params, timeout=45, headers=UA)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"{method}: {body['error']}")
    return body.get("result")


def capture() -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    out: dict = {
        "_provenance": {
            "capturado_em": now.isoformat(timespec="seconds"),
            "fonte_opcoes": f"{DERIBIT}/get_book_summary_by_currency (currency=BTC, kind=option)",
            "fonte_velas": f"{COINBASE}/products/BTC-USD/candles (granularity=86400)",
            "fonte_dvol": f"{DERIBIT}/get_volatility_index_data (currency=BTC, resolution=86400)",
            "nota": ("Dados públicos, sem autenticação. Deribit não restringe o Brasil; "
                     "Binance fica FORA da cadeia de fallback por geo-bloqueio no "
                     "IP do Render free (EUA)."),
        }
    }
    with httpx.Client(follow_redirects=True) as client:
        # ---------------------------------------------------------- 1. velas
        print("[1/4] Coinbase BTC-USD, 30 velas diárias ...")
        end = now
        start = end - dt.timedelta(days=31)
        r = client.get(f"{COINBASE}/products/BTC-USD/candles",
                       params={"granularity": 86400,
                               "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")},
                       timeout=45, headers=UA)
        r.raise_for_status()
        rows = r.json()
        # ATENÇÃO ao formato da Coinbase: [time, LOW, HIGH, open, close, volume]
        # — LOW vem ANTES de HIGH, e a resposta chega em ordem DESCENDENTE.
        candles = [{"ts": x[0] * 1000, "o": x[3], "h": x[2], "l": x[1], "c": x[4], "v": x[5]}
                   for x in rows]
        candles.sort(key=lambda c: c["ts"])
        out["candles_btc"] = candles
        print(f"      {len(candles)} velas · último close {candles[-1]['c']}")

        # ------------------------------------------------------- 2. cadeia
        print("[2/4] Deribit get_book_summary_by_currency BTC ...")
        res = _rpc(client, "get_book_summary_by_currency", currency="BTC", kind="option")
        out["deribit_estimated_delivery_price"] = (
            res[0].get("estimated_delivery_price") if res else None)
        # guarda uma amostra representativa: os vencimentos mais líquidos, mais
        # os casos-limite que quebraram o parser (dia de 1 dígito, bid nulo, OI 0)
        by_exp: dict[str, list] = {}
        for row in res:
            by_exp.setdefault(row["instrument_name"].split("-")[1], []).append(row)
        sample: list[dict] = []
        ranked = sorted(by_exp.items(), key=lambda kv: -sum(
            (x.get("open_interest") or 0) for x in kv[1]))
        for _, rows_e in ranked[:4]:                      # 4 vencimentos mais líquidos
            rows_e = sorted(rows_e, key=lambda x: -(x.get("open_interest") or 0))
            sample.extend(rows_e[:3])
        for row in res:                                   # + casos-limite deliberados
            name = row["instrument_name"]
            day = name.split("-")[1][:-5]
            if (row.get("bid_price") is None or (row.get("open_interest") or 0) == 0
                    or len(day) == 1) and row not in sample:
                sample.append(row)
            if len(sample) >= 24:
                break
        out["options_btc_sample"] = sample
        print(f"      {len(res)} opções no total · amostra de {len(sample)} "
              f"(inclui dia de 1 dígito, bid nulo e OI zero de propósito)")

        # ---------------------------------------------------------- 3. DVOL
        print("[3/4] Deribit DVOL: janela atual + janela histórica ...")
        cur = _rpc(client, "get_volatility_index_data", currency="BTC", resolution=86400,
                   start_timestamp=int((now - dt.timedelta(days=120)).timestamp() * 1000),
                   end_timestamp=int(now.timestamp() * 1000))
        out["dvol_btc_2026_ohlcv"] = cur["data"] if isinstance(cur, dict) else cur
        closes = [r[4] for r in out["dvol_btc_2026_ohlcv"]]
        out["dvol_btc_current"] = {
            "date": dt.datetime.fromtimestamp(
                out["dvol_btc_2026_ohlcv"][-1][0] / 1000, dt.UTC).isoformat(),
            "value": closes[-1],
            "note": "DVOL BTC oficial da Deribit no dia da captura",
        }
        print(f"      {len(closes)} pontos · último {closes[-1]} "
              f"(mín {min(closes)}, máx {max(closes)})")

        hist = _rpc(client, "get_volatility_index_data", currency="BTC", resolution=86400,
                    start_timestamp=1704067200000,          # 2024-01-01, piso duro da API
                    end_timestamp=int((now - dt.timedelta(days=400)).timestamp() * 1000))
        hrows = hist["data"] if isinstance(hist, dict) else hist
        out["dvol_btc_2024_window"] = {
            "note": ("JANELA HISTÓRICA, NÃO é dado atual. Serve para exercitar o "
                     "Ornstein-Uhlenbeck em regime de vol alta e provar que o motor "
                     "LÊ o regime em vez de decorar um número."),
            "start_ts": hrows[0][0] if hrows else None,
            "close": [r[4] for r in hrows],
        }
        print(f"      histórico: {len(hrows)} pontos a partir de "
              f"{dt.datetime.fromtimestamp(hrows[0][0]/1000, dt.UTC).date() if hrows else '?'}")

        # ----------------------------------------------------------- 4. HV
        print("[4/4] Deribit get_historical_volatility (conferência) ...")
        try:
            hv = _rpc(client, "get_historical_volatility", currency="BTC")
            out["deribit_hv_sample_last_values_pct"] = [r[1] for r in (hv or [])][-10:]
            print(f"      últimos: {out['deribit_hv_sample_last_values_pct'][-3:]}")
        except Exception as e:                            # HV é só conferência
            print(f"      HV indisponível ({e}) — não bloqueia a captura")

    # ------------------------------------------------------------ âncoras
    # O modo demo reancora nestes números. Se eles mudarem, o validate_etapa0
    # falha de propósito até alguém reancorar demo_dvol/demo_chain/demo_candles.
    out["_anchors_para_o_demo"] = {
        "spot_btc": out["candles_btc"][-1]["c"] if out["candles_btc"] else None,
        "dvol_btc": out["dvol_btc_current"]["value"],
        "rv_btc_30d_observada": ("rodar volread.realized_vol(candles_btc) para obter; "
                                 "em 26/set/2026 deu 39,18% com ann_vol 0,363 no gerador"),
        "acao": ("se algum destes valores se afastar muito do que está hardcoded em "
                 "provider.demo_*, atualize as âncoras lá — não afrouxe o teste"),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="captura e relata, não regrava")
    ap.add_argument("--check", action="store_true",
                    help="captura e compara com o fixture existente")
    a = ap.parse_args()

    try:
        data = capture()
    except Exception as e:
        print(f"\nFALHA NA CAPTURA: {type(e).__name__}: {e}")
        # não culpar a rede por bug de código: distinguir os dois casos
        if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout,
                          httpx.ReadTimeout, httpx.NetworkError)):
            print("Isso é erro de CONEXÃO. Se você está dentro do sandbox do Arena,")
            print("é esperado: não há egresso para Deribit/Coinbase via httpx.")
            print("Rode na sua máquina ou no Render.")
        else:
            print("Isso NÃO é erro de rede — é bug neste script ou resposta")
            print("inesperada da API. Não atribua ao sandbox antes de verificar.")
        return 1

    if a.check and FIX.exists():
        old = json.loads(FIX.read_text(encoding="utf-8"))
        print("\n=== comparação com o fixture existente ===")
        pairs = [
            ("spot BTC", (old.get("candles_btc") or [{}])[-1].get("c"),
             (data.get("candles_btc") or [{}])[-1].get("c")),
            ("DVOL BTC", (old.get("dvol_btc_current") or {}).get("value"),
             (data.get("dvol_btc_current") or {}).get("value")),
            ("n opções na amostra", len(old.get("options_btc_sample") or []),
             len(data.get("options_btc_sample") or [])),
            ("n velas", len(old.get("candles_btc") or []), len(data.get("candles_btc") or [])),
        ]
        drift = False
        for label, o, n in pairs:
            d = ""
            if isinstance(o, (int, float)) and isinstance(n, (int, float)) and o:
                pct = abs(n - o) / abs(o) * 100
                d = f"  ({pct:+.1f}%)"
                if pct > 20:
                    d += "  <-- DRIFT GRANDE: reancorar o demo"
                    drift = True
            print(f"  {label:22s} {o} -> {n}{d}")
        print("\nâncoras do demo precisam de atualização." if drift
              else "\nâncoras ainda coerentes com o demo.")

    if a.dry_run:
        print("\n--dry-run: nada foi gravado.")
        return 0

    FIX.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"\ngravado: {FIX} ({FIX.stat().st_size/1024:.0f} KB)")
    print("agora rode: python -m tools.validate_etapa0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
