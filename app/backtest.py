"""Backtesting v3 do FutAnalytics — com ROI, CLV e intervalo de confiança.

Diferença de fundo em relação à v2: aqui **toda** estratégia é medida em
dinheiro, não em taxa de acerto. A pergunta não é "o modelo acerta?", é
"apostando o que ele manda, o capital cresce?".

O que o relatório responde, com dados reais de 5 ligas:

1. **Probabilidade**: Brier/LogLoss do modelo (cru, calibrado, misturado) e do
   mercado, por mercado, com o baseline ingênuo.
2. **Calibração** por faixa e teste de alfa (quem tem razão quando o modelo
   diverge do mercado).
3. **Políticas comparadas em dinheiro** (odds de abertura e de fechamento):
   - ``mais provável`` — dinâmica de produção da v2 (aposta o desfecho mais
     provável de gols em cada jogo);
   - ``valor+faixa`` — política v3 (valor esperado, vantagem sobre o mercado
     devigado, faixa de odd e stake de Kelly com incerteza);
   - ``valor só-modelo`` — igual, sem misturar com o mercado;
   - ``só mercado`` — mesma política de valor, mas com probabilidades do
     próprio mercado (controle: mede se o modelo agrega algo);
   - ``favorito O/U 2.5`` — baseline ingênuo.
   Para cada uma: nº de apostas, acerto, **ROI nas odds de abertura**, ROI nas
   de fechamento, **CLV médio** (% que bateu a linha de fechamento) e o
   **intervalo de confiança de 95%** por bootstrap do ROI — sem o qual
   qualquer ROI de amostra pequena é ruído.
4. **ROI por faixa de odd** — a evidência empírica de onde o dinheiro vive.
5. **Múltiplas**: acerto e ROI de bilhetes de 2 a 5 pernas, com a margem
   efetiva de cada tamanho.
6. **Kelly na prática**: capital final, drawdown máximo e risco de ruína
   comparando stake fixa × Kelly fracionado.
7. **Métodos de devig**: qual remove melhor a margem (Brier e ROI).

Tudo com **blindagem de vazamento**: histórico e ajuste do modelo por liga
usam apenas jogos anteriores à partida avaliada; a calibração é treinada nas
temporadas de treino e aplicada (sem reajuste) nas de teste.

Uso típico::

    python -m app.backtest --train 1920 2021 --test 2122 2223 --report backtest_report.md
    python -m app.backtest --train 1920 2021 2122 2223 --test 2324 2425 2526 \\
        --fit-calibration --report backtest_report.md

Dados: CSVs do football-data.co.uk (odds de abertura e fechamento). O sandbox
deste repositório pode não ter acesso à internet; nesse caso aponte
``FUTA_DATA_DIR`` para um espelho local no formato ``<temporada>/<DIV>.csv``
(ver ``scripts/fetch_football_data.py``). O xG do Understat é opcional
(``FUTA_XG_DIR`` com os CSVs de chutes) — sem ele o backtest roda no modo
"gols", que é o piso conservador do sistema.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import os
import random
import statistics
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from . import calibration as calib
from . import joint, understat
from . import market as mk
from .model import PARAMS, TeamSample, analyze_match, blend_markets, market_candidates, pick_best_market

FD_BASE = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

FD_LEAGUES = {
    "E0": "Premier League",
    "SP1": "La Liga",
    "I1": "Serie A (Itália)",
    "D1": "Bundesliga",
    "F1": "Ligue 1",
}
MIN_HIST = 8
TRAIN_DAYS_BACK = 240

# odds usadas como preço de entrada (abertura B365) e referência (fechamento)
OPEN_COLS = {"home": "B365H", "draw": "B365D", "away": "B365A",
             "over_2.5": "B365>2.5", "under_2.5": "B365<2.5"}
CLOSE_COLS = {"home": "B365CH", "draw": "B365CD", "away": "B365CA",
              "over_2.5": "B365C>2.5", "under_2.5": "B365C<2.5"}

MARKET_OUTCOME = {
    "over_2.5": lambda hg, ag: 1.0 if hg + ag >= 3 else 0.0,
    "under_2.5": lambda hg, ag: 1.0 if hg + ag <= 2 else 0.0,
    "over_1.5": lambda hg, ag: 1.0 if hg + ag >= 2 else 0.0,
    "under_1.5": lambda hg, ag: 1.0 if hg + ag <= 1 else 0.0,
    "over_3.5": lambda hg, ag: 1.0 if hg + ag >= 4 else 0.0,
    "under_3.5": lambda hg, ag: 1.0 if hg + ag <= 3 else 0.0,
    "ht_0.5": lambda hg, ag: 1.0 if hg >= 1 else 0.0,
    "at_0.5": lambda hg, ag: 1.0 if ag >= 1 else 0.0,
    "ht_1.5": lambda hg, ag: 1.0 if hg >= 2 else 0.0,
    "at_1.5": lambda hg, ag: 1.0 if ag >= 2 else 0.0,
    "btts_yes": lambda hg, ag: 1.0 if hg > 0 and ag > 0 else 0.0,
    "btts_no": lambda hg, ag: 1.0 if hg == 0 or ag == 0 else 0.0,
    "home": lambda hg, ag: 1.0 if hg > ag else 0.0,
    "draw": lambda hg, ag: 1.0 if hg == ag else 0.0,
    "away": lambda hg, ag: 1.0 if ag > hg else 0.0,
}


# ------------------------------------------------------------------ dados


def _season_dir_candidates(season: str) -> list[Path]:
    roots = []
    env = os.environ.get("FUTA_DATA_DIR")
    if env:
        roots.append(Path(env))
    roots += [CACHE_DIR / "football_data", CACHE_DIR, Path.cwd() / "data_mirror"]
    out = []
    for r in roots:
        for sub in ("", "data/raw/football_data", "football_data"):
            out.append(r / sub / season)
    return out


def load_fd_season(div: str, season: str) -> list[dict]:
    """Jogos de uma temporada/divisão com odds de abertura e fechamento.

    Procura primeiro em espelho local (``FUTA_DATA_DIR``, ``data_cache/``),
    depois baixa de football-data.co.uk e guarda em cache.
    """
    text = None
    for d in _season_dir_candidates(season):
        f = d / f"{div}.csv"
        if f.exists():
            text = f.read_text(encoding="utf-8", errors="replace")
            break
    if text is None:
        cache_file = CACHE_DIR / "football_data" / f"{season}_{div}.csv"
        if cache_file.exists():
            text = cache_file.read_text(encoding="utf-8", errors="replace")
        else:
            url = FD_BASE.format(season=season, div=div)
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": understat.US_HEADERS["User-Agent"]})
                with urllib.request.urlopen(req, timeout=40) as r:
                    text = r.read().decode("utf-8", errors="replace")
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(text, encoding="utf-8")
            except Exception as e:  # rede indisponível: segue sem esta liga
                print(f"  ! {div} {season}: {e}", file=sys.stderr)
                return []

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        date = _parse_fd_date(row.get("Date", ""))
        if not date:
            continue
        try:
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
        except (KeyError, TypeError, ValueError):
            continue

        def _col(src: dict, col: str):
            try:
                v = float(src.get(col) or 0)
                return v if v > 1.01 else None
            except (TypeError, ValueError):
                return None

        odds = {k: _col(row, c) for k, c in OPEN_COLS.items()}
        closing = {k: _col(row, c) for k, c in CLOSE_COLS.items()}
        out.append({
            "league": FD_LEAGUES[div], "div": div, "season": season, "date": date,
            "home": row["HomeTeam"], "away": row["AwayTeam"], "hg": hg, "ag": ag,
            "odds": {k: v for k, v in odds.items() if v},
            "closing": {k: v for k, v in closing.items() if v},
        })
    out.sort(key=lambda m: m["date"])
    return out


def _parse_fd_date(s: str) -> dt.date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def load_xg_matches() -> dict:
    """xG por jogo a partir de CSVs de chutes do Understat (opcional).

    Aceita qualquer diretório com CSVs de chutes contendo as colunas
    ``h_team, a_team, date, xG, h_a, match_id, h_goals, a_goals``.
    Devolve índice por (time_casa_normalizado, time_fora, data).
    """
    root = os.environ.get("FUTA_XG_DIR")
    if not root:
        return {}
    root = Path(root)
    if not root.exists():
        return {}
    season_filter = {v.strip() for v in os.environ.get("FUTA_XG_SEASONS", "").split(",") if v.strip()}
    cache_file = CACHE_DIR / "xg_index.json"
    acc: dict = defaultdict(lambda: {"xgh": 0.0, "xga": 0.0})
    meta: dict = {}
    csvs = [root] if root.is_file() else sorted(root.rglob("shots_*.csv")) or sorted(root.rglob("*.csv"))
    n_files = 0
    for f in csvs:
        try:
            with f.open(newline="", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                if not reader.fieldnames or "xG" not in reader.fieldnames:
                    continue
                n_files += 1
                for row in reader:
                    if season_filter:
                        # a coluna "season" do Understat é o ano de INÍCIO da
                        # temporada (2021 = 2021/22); usar o ano da data
                        # misturaria duas temporadas e cortaria a cobertura
                        season = (row.get("season") or "").strip() or (row.get("date") or "")[:4]
                        if season and season not in season_filter:
                            continue
                    mid = row.get("match_id") or f"{row.get('date')}-{row.get('h_team')}-{row.get('a_team')}"
                    key = str(mid)
                    try:
                        xg = float(row.get("xG") or 0.0)
                    except ValueError:
                        continue
                    if row.get("h_a") == "h":
                        acc[key]["xgh"] += xg
                    elif row.get("h_a") == "a":
                        acc[key]["xga"] += xg
                    meta.setdefault(key, {
                        "home": understat.normalize_name(row.get("h_team") or ""),
                        "away": understat.normalize_name(row.get("a_team") or ""),
                        "date": (row.get("date") or "")[:10],
                        "hg": row.get("h_goals"), "ag": row.get("a_goals"),
                    })
        except OSError:
            continue
    if not n_files:
        return {}
    index = {}
    titles = set()
    for key, agg in acc.items():
        m = meta.get(key)
        if not m or not m["date"]:
            continue
        index[f"{m['home']}|{m['away']}|{m['date']}"] = {
            "xgh": round(agg["xgh"], 3), "xga": round(agg["xga"], 3)}
        titles.add(m["home"])
        titles.add(m["away"])
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"n": len(index)}))
    except OSError:
        pass
    return {"index": index, "titles": sorted(titles)}


def attach_xg(matches: list[dict], xg_data: dict) -> int:
    """Cola xG do Understat nos jogos do football-data (nome resolvido + data ±1 dia).

    A resolução de nomes usa a mesma tabela de apelidos do motor em produção
    (``understat.match_understat_team``), então o que é validado aqui é o mesmo
    casamento que roda no painel.
    """
    index = (xg_data or {}).get("index") or {}
    titles = (xg_data or {}).get("titles") or []
    if not index or not titles:
        return 0
    n = 0
    for m in matches:
        us_h = understat.match_understat_team(m["home"], titles)
        us_a = understat.match_understat_team(m["away"], titles)
        if not us_h or not us_a:
            continue
        h = understat.normalize_name(us_h)
        a = understat.normalize_name(us_a)
        for delta in (0, 1, -1):
            key = f"{h}|{a}|{(m['date'] + dt.timedelta(days=delta)).isoformat()}"
            hit = index.get(key)
            if hit:
                m["xgh"], m["xga"] = hit["xgh"], hit["xga"]
                n += 1
                break
    return n


# ------------------------------------------------------------------ pipeline


def build_histories(matches: list[dict]) -> dict:
    hist: dict = defaultdict(list)
    for _i, m in enumerate(matches):
        xgh, xga = m.get("xgh"), m.get("xga")
        hist[m["home"]].append((m["date"], True, m["hg"], m["ag"], xgh, xga, m["away"]))
        hist[m["away"]].append((m["date"], False, m["hg"], m["ag"], xga, xgh, m["home"]))
    return hist


def team_games(hist, team, today, n=40):
    """Jogos anteriores a ``today``, mais recente primeiro (sem vazamento)."""
    games = []
    for date, is_home, gf, ga, xgf, xga, opp in reversed(hist.get(team, [])):
        if date >= today:
            continue
        days = (today - date).days
        if days > TRAIN_DAYS_BACK:
            break
        games.append((days, is_home, gf, ga, xgf, xga, opp))
        if len(games) >= n:
            break
    return list(reversed(games))


def run(matches: list[dict], *, mode: str = "joint", calibrators: dict | None = None,
        model_weight: float = 0.25, devig_method: str = "power",
        joint_refit_days: int = 7) -> list[dict]:
    """Reproduz o pipeline do app jogo a jogo, sem vazamento.

    ``mode``: ``joint`` (modelo conjunto por liga, produção) | ``xg`` (estimador
    por time com xG) | ``goals`` (só gols brutos).
    """
    xg_weight = 0.0 if mode == "goals" else PARAMS["XG_WEIGHT"]
    hist = build_histories(matches)
    by_league: dict = defaultdict(list)
    for m in matches:
        by_league[m["league"]].append(m)
    joint_cache: dict = {}
    results = []

    for m in matches:
        gh = team_games(hist, m["home"], m["date"])
        ga = team_games(hist, m["away"], m["date"])
        if len(gh) < MIN_HIST or len(ga) < MIN_HIST:
            continue
        pool = [g[:6] for g in gh] + [g[:6] for g in ga]
        from .model import league_priors, league_xg_priors
        home_avg, away_avg = league_priors(pool)
        xg_home_avg, xg_away_avg = league_xg_priors(pool)

        lam_override = None
        ratings = None
        if mode == "joint":
            key = (m["league"], m["date"].toordinal() // joint_refit_days)
            if key not in joint_cache:
                joint_cache[key] = joint.fit_league(by_league[m["league"]], m["date"])
            model = joint_cache[key]
            if model:
                lam_override = joint.predict_lambdas(model, m["home"], m["away"])
                ratings = model
        res = analyze_match(TeamSample(m["home"], gh), TeamSample(m["away"], ga),
                            home_avg, away_avg, xg_home_avg, xg_away_avg,
                            xg_weight=xg_weight, lam_override=lam_override)
        raw = dict(res["markets"])
        calibrated = raw
        if calibrators:
            calibrated = calib.apply_calibration(raw, calibrators)
        blended = calibrated
        if m["odds"]:
            blended = blend_markets(calibrated, m["odds"], model_weight, method=devig_method)
        results.append({
            **m, "raw": raw, "calibrated": calibrated, "probs": blended,
            "n_eff": (res["sample_home"] + res["sample_away"]) / 2,
            "xg_used": res.get("xg_used", False),
            "joint_model": lam_override is not None,
            "ratings": ratings,
        })
    return results


# ------------------------------------------------------------------ métricas


def outcome(r: dict, market_key: str) -> float:
    fn = MARKET_OUTCOME.get(market_key)
    return fn(r["hg"], r["ag"]) if fn else 0.0


def brier(preds, reals) -> float:
    return sum((p - y) ** 2 for p, y in zip(preds, reals, strict=False)) / len(preds)


def logloss(preds, reals, eps=1e-6) -> float:
    return -sum(math.log(max(p if y else 1 - p, eps)) for p, y in zip(preds, reals, strict=False)) / len(preds)


MARKET_PAIRS = (("home", "draw", "away"), ("over_2.5", "under_2.5"),
                ("over_1.5", "under_1.5"))


def devig_market(r: dict, market_key: str, method: str = "power",
                 source: str = "odds") -> float | None:
    """Probabilidade devigada do mercado para o desfecho pedido.

    ``source``: ``odds`` (abertura) ou ``closing`` (fechamento) — o fechamento
    alimenta o benchmark "oráculo da linha".
    """
    group = next((g for g in MARKET_PAIRS if market_key in g), None)
    if not group:
        return None
    odds = r.get(source) or {}
    if not all(odds.get(k) and odds[k] > 1.0 for k in group):
        return None
    qs = mk.devig([odds[k] for k in group], method)
    for k, q in zip(group, qs, strict=False):
        if k == market_key:
            return q
    return None


def bootstrap_ci(values: list[float], n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 42) -> tuple[float, float]:
    """IC de 95% do ROI médio por bootstrap (reamostragem com reposição)."""
    if len(values) < 5:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += values[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(alpha / 2 * n_boot)]
    hi = means[int((1 - alpha / 2) * n_boot) - 1]
    return (lo, hi)


def simulate_bets(bets: list[dict], *, kelly_mult: float = 0.25, cap: float = 0.03,
                  bankroll0: float = 1000.0, beta_quantile: float = 0.30) -> dict:
    """ROI (flat), CLV, Kelly com curva de capital e drawdown."""
    if not bets:
        return {"n": 0}
    profits_flat = []
    clvs = []
    wins = 0
    bank_flat = bank_kelly = bankroll0
    peak = bank_kelly
    max_dd = 0.0
    staked_kelly = 0.0
    for b in bets:
        won = b["y"] > 0.5
        wins += won
        profit = (b["odd"] - 1.0) if won else -1.0
        profits_flat.append(profit)
        bank_flat += profit
        st = mk.stake_fraction(b["prob"], b["odd"], kelly_mult=kelly_mult, cap=cap,
                               n_eff=b.get("n_eff"), beta_quantile=beta_quantile)
        stake = bank_kelly * st["kelly_frac"]
        staked_kelly += stake
        bank_kelly += stake * ((b["odd"] - 1.0) if won else -1.0)
        peak = max(peak, bank_kelly)
        max_dd = max(max_dd, (peak - bank_kelly) / peak if peak > 0 else 0.0)
        if b.get("closing_odd"):
            clvs.append((b["odd"] / b["closing_odd"] - 1.0) * 100.0)
        b["_won"] = won
    roi = 100.0 * sum(profits_flat) / len(profits_flat)
    lo, hi = bootstrap_ci(profits_flat)
    return {
        "n": len(bets),
        "hit": 100.0 * wins / len(bets),
        "roi": roi,
        "roi_ci": (100.0 * lo, 100.0 * hi) if lo == lo else (float("nan"), float("nan")),
        "units": sum(profits_flat),
        "kelly_final": bank_kelly,
        "kelly_roi": 100.0 * (bank_kelly - bankroll0) / bankroll0,
        "kelly_max_dd": 100.0 * max_dd,
        "clv_mean": statistics.mean(clvs) if clvs else None,
        "clv_beat": 100.0 * sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None,
        "clv_n": len(clvs),
    }


# ------------------------------------------------------------------ políticas


def policy_most_probable(r: dict) -> dict | None:
    """Dinâmica de produção da v2: o mercado de gols mais provável do jogo."""
    best = pick_best_market({"markets": r["probs"], "confidence": 0.9},
                            r["odds"], mode="prob")
    if not best:
        return None
    if not (r["odds"] or {}).get(best["market"]):
        return None
    return {"market": best["market"], "prob": best["prob"], "odd": best["odd"],
            "family": "gols"}


def policy_value(r: dict, *, probs_key: str = "probs", mode: str = "singles",
                 devig_method: str = "power", only: tuple | None = None) -> dict | None:
    """Política v3: valor + faixa de odd + vantagem sobre o mercado.

    ``only`` restringe a um subconjunto de mercados (usado no teste exploratório
    do único sinal de CLV positivo encontrado: o mercado CASA com xG).
    """
    cards = market_candidates({"markets": r[probs_key]}, r["odds"], mode=mode,
                              n_eff=r["n_eff"], devig_method=devig_method, markets=only)
    for c in cards:
        if c["take"] and (r["odds"] or {}).get(c["market"]):
            return {"market": c["market"], "prob": c["prob"], "odd": c["odd"],
                    "ev": c["ev"], "edge": c["edge"], "growth": c["growth"],
                    "family": c["family"], "n_eff": r["n_eff"]}
    return None


def policy_favorite_ou(r: dict) -> dict | None:
    """Baseline: Over 2.5 se o mercado (devigado) aponta mais de 2.5 gols."""
    q_over = devig_market(r, "over_2.5")
    if q_over is None:
        return None
    if q_over >= 0.5:
        return {"market": "over_2.5", "prob": q_over, "odd": r["odds"]["over_2.5"],
                "family": "gols"}
    return {"market": "under_2.5", "prob": 1 - q_over, "odd": r["odds"]["under_2.5"],
            "family": "gols"}


def policy_closing_oracle(r: dict) -> dict | None:
    """Benchmark: e se o modelo soubesse a linha de FECHAMENTO?

    Usa a probabilidade devigada do fechamento e aposta na odd de abertura.
    É o teto de referência do mercado: mede quanto de valor existe entre a
    abertura e o fechamento — e, portanto, quanto um modelo precisaria
    "enxergar" para ganhar dinheiro de forma consistente.
    """
    best = None
    for market_key in ("home", "draw", "away", "over_2.5", "under_2.5"):
        q = devig_market(r, market_key, source="closing")
        if q is None:
            continue
        odd = (r.get("odds") or {}).get(market_key)
        if not odd:
            continue
        ev = q * odd - 1.0
        if ev < 0.02:
            continue
        if not (0.50 <= q <= 0.85) or not (1.45 <= odd <= 3.20):
            continue
        if best is None or ev > best["ev"]:
            best = {"market": market_key, "prob": q, "odd": odd, "ev": ev,
                    "family": "mercado"}
    return best


POLICIES = {
    "mais provável (v2)": lambda r: policy_most_probable(r),
    "casa valor+faixa (exp.)": lambda r: policy_value(r, probs_key="calibrated",
                                                       only=("home",)),
    "valor+faixa (v3)": lambda r: policy_value(r),
    "valor só-modelo": lambda r: policy_value(r, probs_key="calibrated"),
    "oráculo do fechamento": lambda r: policy_closing_oracle(r),
    "favorito O/U 2.5": lambda r: policy_favorite_ou(r),
}


def collect_bets(results: list[dict], policy_fn, *, max_ev: float = 0.25) -> list[dict]:
    bets = []
    for r in results:
        pick = policy_fn(r)
        if not pick:
            continue
        if pick.get("ev") is not None and pick["ev"] > max_ev:
            continue  # EV absurdo = suspeita de erro de dado
        odd = (r["odds"] or {}).get(pick["market"])
        if not odd:
            continue
        bets.append({
            "market": pick["market"], "prob": pick["prob"], "odd": odd,
            "y": outcome(r, pick["market"]), "date": r["date"],
            "league": r["league"], "season": r["season"], "div": r["div"],
            "closing_odd": (r["closing"] or {}).get(pick["market"]),
            "n_eff": pick.get("n_eff") or r.get("n_eff"),
            "ev": pick.get("ev"), "edge": pick.get("edge"),
        })
    return bets


def market_probs(r: dict, method: str = "power") -> dict:
    """Probabilidades do próprio mercado (para o controle "só mercado")."""
    out = {}
    for key in ("home", "draw", "away"):
        q = devig_market(r, key, method)
        if q is not None:
            out[key] = q
    q_over = devig_market(r, "over_2.5", method)
    if q_over is not None:
        out["over_2.5"] = q_over
        out["under_2.5"] = 1 - q_over
    odds = r.get("odds") or {}
    if odds.get("over_1.5") and odds.get("under_1.5"):
        qs = mk.devig([odds["over_1.5"], odds["under_1.5"]], method)
        out["over_1.5"], out["under_1.5"] = qs[0], qs[1]
    return out


# ------------------------------------------------------------------ relatório


def _fmt_ci(lo: float, hi: float) -> str:
    if lo != lo:
        return "—"
    return f"[{lo:+.1f}%, {hi:+.1f}%]"


def block_probability(results: list[dict], label: str) -> str:
    lines = [f"### {label}", "",
             "| Mercado | Brier ingênuo | Brier modelo | Brier calibrado | Brier +mercado | "
             "Brier mercado | LogLoss modelo | LogLoss mercado |",
             "|---|---|---|---|---|---|---|---|"]
    for market_key in ("over_2.5", "home", "away", "draw"):
        rows = []
        for r in results:
            q = devig_market(r, market_key)
            if q is None:
                continue
            rows.append((r, q))
        if not rows:
            continue
        reals = [outcome(r, market_key) for r, _ in rows]
        base_rate = sum(reals) / len(reals)
        b_naive = brier([base_rate] * len(reals), reals)
        b_raw = brier([r["raw"][market_key] for r, _ in rows], reals)
        b_cal = brier([r["calibrated"][market_key] for r, _ in rows], reals)
        b_blend = brier([r["probs"][market_key] for r, _ in rows], reals)
        b_mkt = brier([q for _, q in rows], reals)
        ll_raw = logloss([r["raw"][market_key] for r, _ in rows], reals)
        ll_mkt = logloss([q for _, q in rows], reals)
        lines.append(f"| {market_key} | {b_naive:.4f} | {b_raw:.4f} | {b_cal:.4f} | "
                     f"{b_blend:.4f} | {b_mkt:.4f} | {ll_raw:.4f} | {ll_mkt:.4f} |")
    lines.append("")
    lines.append(f"*Baseline ingênuo (frequência base): "
                 f"{sum(outcome(r, 'over_2.5') for r in results) / max(len(results), 1):.3f} "
                 f"de Over 2.5 na amostra.*")
    lines.append("")
    return "\n".join(lines)


def block_alpha(results: list[dict]) -> str:
    rows = []
    for r in results:
        q = devig_market(r, "over_2.5")
        if q is None:
            continue
        rows.append((r["calibrated"]["over_2.5"] - q, outcome(r, "over_2.5"), q))
    if not rows:
        return ""
    rows.sort(key=lambda t: t[0])
    q = max(len(rows) // 5, 1)
    lines = ["**Teste de alfa (Over 2.5):** quando o modelo diverge do mercado, quem acerta?", "",
             "| Quintil | Divergência (modelo−mercado) | Mercado diz | Real |", "|---|---|---|---|"]
    for i in range(5):
        chunk = rows[i * q:(i + 1) * q] if i < 4 else rows[4 * q:]
        if not chunk:
            continue
        dm = statistics.mean(c[0] for c in chunk)
        real = statistics.mean(c[1] for c in chunk)
        mkt = statistics.mean(c[2] for c in chunk)
        lines.append(f"| Q{i+1} | {dm:+.3f} | {mkt:.3f} | {real:.3f} |")
    lines.append("")
    return "\n".join(lines)


def block_strategies(results: list[dict], *, kelly_mult: float = 0.25,
                     cap_pct: float = 3.0) -> str:
    lines = ["### Políticas em dinheiro (odds de abertura; stake Kelly fracionado)", "",
             "| Política | Apostas | Acerto | ROI | IC 95% do ROI | ROI Kelly | Drawdown máx | CLV médio | Bateu fechamento |",
             "|---|---|---|---|---|---|---|---|---|"]
    details = {}
    for name, fn in POLICIES.items():
        bets = collect_bets(results, fn)
        sim = simulate_bets(bets, kelly_mult=kelly_mult, cap=cap_pct / 100.0)
        details[name] = (bets, sim)
        if not sim.get("n"):
            lines.append(f"| {name} | 0 | — | — | — | — | — | — | — |")
            continue
        clv = f"{sim['clv_mean']:+.2f}%" if sim["clv_mean"] is not None else "—"
        beat = f"{sim['clv_beat']:.1f}% ({sim['clv_n']})" if sim["clv_beat"] is not None else "—"
        lines.append(
            f"| {name} | {sim['n']} | {sim['hit']:.1f}% | {sim['roi']:+.2f}% | "
            f"{_fmt_ci(*sim['roi_ci'])} | {sim['kelly_roi']:+.2f}% | "
            f"{sim['kelly_max_dd']:.1f}% | {clv} | {beat} |")
    lines.append("")

    # ROI por faixa de odd (todos os candidatos que passaram o filtro de valor)
    bets = details.get("valor+faixa (v3)", ([], {}))[0]
    if bets:
        buckets = [(1.0, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 99)]
        lines += ["**ROI por faixa de odd (política v3, odds de abertura)** — onde o dinheiro vive:", "",
                  "| Faixa de odd | Apostas | Acerto | ROI | IC 95% |", "|---|---|---|---|---|"]
        for lo, hi in buckets:
            sel = [b for b in bets if lo <= b["odd"] < hi]
            if not sel:
                continue
            profits = [100.0 * ((b["odd"] - 1) if b["y"] > 0.5 else -1) for b in sel]
            ci = bootstrap_ci(profits)
            lines.append(f"| {lo:.2f}–{hi:.2f} | {len(sel)} | "
                         f"{100 * sum(1 for p in profits if p > 0) / len(sel):.1f}% | "
                         f"{statistics.mean(profits):+.2f}% | {_fmt_ci(*ci)} |")
        lines.append("")

    # múltiplas: bilhetes de k pernas montados como o app monta
    bets = details.get("valor+faixa (v3)", ([], {}))[0]
    if bets:
        by_day = defaultdict(list)
        for b in bets:
            by_day[b["date"]].append(b)
        lines += ["**Múltiplas (pernas elegíveis por dia, montadas por crescimento esperado):**", "",
                  "| Pernas | Bilhetes | Acerto | Odd média | ROI | Margem efetiva média |",
                  "|---|---|---|---|---|---|"]
        for k in (2, 3, 4, 5):
            slips = []
            for _day, day_bets in by_day.items():
                pool = sorted(day_bets, key=lambda b: -(b.get("growth") or 0))[:k]
                if len(pool) < k:
                    continue
                odd = math.prod(b["odd"] for b in pool)
                prob = math.prod(b["prob"] for b in pool)
                won = all(b["y"] > 0.5 for b in pool)
                margins = mk.effective_margin([b["odd"] for b in pool],
                                              [1 / b["odd"] for b in pool])
                slips.append({"odd": odd, "prob": prob, "won": won, "margin": margins})
            if not slips:
                continue
            profits = [100.0 * ((s["odd"] - 1) if s["won"] else -1) for s in slips]
            ci = bootstrap_ci(profits)
            lines.append(f"| {k} | {len(slips)} | "
                         f"{100 * sum(1 for s in slips if s['won']) / len(slips):.1f}% | "
                         f"{statistics.mean(s['odd'] for s in slips):.2f} | "
                         f"{statistics.mean(profits):+.2f}% {_fmt_ci(*ci)} | "
                         f"{100 * statistics.mean(s['margin'] for s in slips):+.2f}% |")
        lines.append("")
    return "\n".join(lines)



def ols(xs: list[float], ys: list[float]) -> dict:
    """Regressão linear simples com erro-padrão e t-stat (para o teste de alpha)."""
    n = len(xs)
    if n < 30:
        return {"n": n}
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=False))
    if sxx <= 0:
        return {"n": n}
    beta = sxy / sxx
    alpha = my - beta * mx
    resid = [y - (alpha + beta * x) for x, y in zip(xs, ys, strict=False)]
    sse = sum(r * r for r in resid)
    sst = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sse / sst if sst > 0 else 0.0
    se = math.sqrt(sse / (n - 2) / sxx) if n > 2 and sxx > 0 else float("inf")
    return {"n": n, "beta": beta, "alpha": alpha, "r2": r2,
            "t": beta / se if se > 0 else 0.0}


def block_line_movement(results: list[dict]) -> str:
    """O modelo antecipa o MOVIMENTO da linha? (teste mais duro de alpha)

    O prêmio real do apostador é comprar a abertura e ver o mercado fechar
    mais caro (CLV). Para isso o modelo precisa prever **para onde a linha
    vai**, não quem ganha o jogo. Regressão:

        (q_fechamento − q_abertura) = α + β·(p_modelo − q_abertura)

    β > 0 e significativo ⇒ o modelo contém informação que o mercado ainda
    não precificou na abertura. β ≈ 0 ⇒ divergência do modelo é ruído, e
    apostar nela é pagar a margem por nada.
    """
    lines = ["### O modelo antecipa o fechamento? (alpha em CLV)", "",
             "| Mercado | n | β | erro t | R² | Leitura |", "|---|---|---|---|---|---|"]
    for market_key in ("over_2.5", "home", "away"):
        xs, ys = [], []
        for r in results:
            q_open = devig_market(r, market_key)
            q_close = devig_market(r, market_key, source="closing")
            model_p = r["calibrated"].get(market_key)
            if q_open is None or q_close is None or model_p is None:
                continue
            xs.append(model_p - q_open)
            ys.append(q_close - q_open)
        st = ols(xs, ys)
        if st.get("n", 0) < 30:
            continue
        reading = ("sem alpha detectável" if abs(st["t"]) < 2 else
                   ("o modelo antecipa o movimento" if st["beta"] > 0 else
                    "o modelo é sistematicamente CONTRÁRIO ao movimento"))
        lines.append(f"| {market_key} | {st['n']} | {st['beta']:+.3f} | "
                     f"{st['t']:+.2f} | {st['r2']:.3f} | {reading} |")
    lines.append("")
    lines.append("Referência: |t| < 2 significa que a divergência do modelo "
                 "não prevê o fechamento — apostar nela é, na média, pagar a margem.")
    lines.append("")
    return "\n".join(lines)


def block_devig(results: list[dict]) -> str:
    """Qual método de devig descreve melhor o mercado."""
    lines = ["### Métodos de devig (qual descreve melhor o mercado?)", "",
             "| Método | Brier home | Brier draw | Brier away | Brier Over 2.5 |",
             "|---|---|---|---|---|"]
    per_method: dict = {}
    for method in mk.DEVIG_METHODS:
        vals = defaultdict(list)
        for r in results:
            for market_key in ("home", "draw", "away", "over_2.5"):
                q = devig_market(r, market_key, method)
                if q is None:
                    continue
                vals[market_key].append((q, outcome(r, market_key)))
        per_method[method] = vals
        if not vals:
            continue
        cells = []
        for market_key in ("home", "draw", "away", "over_2.5"):
            pairs = vals.get(market_key)
            cells.append(f"{brier([p for p, _ in pairs], [y for _, y in pairs]):.4f}"
                         if pairs else "—")
        lines.append(f"| {method} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def estimate_correlation(matches: list[dict]) -> dict:
    """Correlação entre Over 2.5 de jogos da mesma liga no mesmo dia.

    Alimenta a correção de correlação das múltiplas (``app/parlay.py``).
    Medida em nível de desfecho: P(A)·P(B) vs P(A∩B) empírico.
    """
    by_day: dict = defaultdict(list)
    for m in matches:
        by_day[(m["league"], m["date"])].append(1.0 if m["hg"] + m["ag"] >= 3 else 0.0)
    pairs = []
    for vals in by_day.values():
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                pairs.append((vals[i], vals[j]))
    if len(pairs) < 200:
        return {"n": len(pairs)}
    p1 = statistics.mean(a for a, _ in pairs)
    p2 = statistics.mean(b for _, b in pairs)
    both = statistics.mean(a * b for a, b in pairs)
    phi = (both - p1 * p2) / math.sqrt(p1 * (1 - p1) * p2 * (1 - p2)) if 0 < p1 < 1 and 0 < p2 < 1 else 0.0
    return {"n": len(pairs), "p_over": round(p1, 4), "joint_obs": round(both, 5),
            "indep": round(p1 * p2, 5), "rho_phi": round(phi, 4),
            "delta": round(both - p1 * p2, 5)}


def summarize(results: list[dict], *, kelly_mult: float = 0.25, cap_pct: float = 3.0,
              devig: str = "power", corr: dict | None = None) -> dict:
    """Resumo estruturado (JSON) do backtest — consumido pela aba Auditoria."""
    out: dict = {"n_matches": len(results), "devig": devig,
                 "generated_at": dt.datetime.now().isoformat(timespec="seconds")}
    # políticas
    pol = {}
    for name, fn in POLICIES.items():
        bets = collect_bets(results, fn)
        sim = simulate_bets(bets, kelly_mult=kelly_mult, cap=cap_pct / 100.0)
        entry = {"n": sim.get("n", 0)}
        if sim.get("n"):
            entry.update({
                "hit": round(sim["hit"], 2),
                "roi": round(sim["roi"], 2),
                "roi_ci": [round(sim["roi_ci"][0], 2), round(sim["roi_ci"][1], 2)]
                if sim["roi_ci"][0] == sim["roi_ci"][0] else None,
                "kelly_roi": round(sim["kelly_roi"], 2),
                "kelly_max_dd": round(sim["kelly_max_dd"], 2),
                "clv_mean": round(sim["clv_mean"], 2) if sim["clv_mean"] is not None else None,
                "clv_beat": round(sim["clv_beat"], 1) if sim["clv_beat"] is not None else None,
            })
        pol[name] = entry
    out["policies"] = pol
    # preditivo
    predictive = {}
    for market_key in ("over_2.5", "home", "away", "draw"):
        rows = [(r, devig_market(r, market_key, devig)) for r in results]
        rows = [(r, q) for r, q in rows if q is not None]
        if not rows:
            continue
        reals = [outcome(r, market_key) for r, _ in rows]
        predictive[market_key] = {
            "model": round(brier([r["raw"][market_key] for r, _ in rows], reals), 4),
            "calibrated": round(brier([r["calibrated"][market_key] for r, _ in rows], reals), 4),
            "blend": round(brier([r["probs"][market_key] for r, _ in rows], reals), 4),
            "market": round(brier([q for _, q in rows], reals), 4),
        }
    out["predictive"] = predictive
    # alpha (CLV)
    alpha = {}
    for market_key in ("over_2.5", "home", "away"):
        xs, ys = [], []
        for r in results:
            q_open = devig_market(r, market_key, devig)
            q_close = devig_market(r, market_key, devig, source="closing")
            if q_open is None or q_close is None:
                continue
            xs.append(r["calibrated"][market_key] - q_open)
            ys.append(q_close - q_open)
        st = ols(xs, ys)
        if st.get("n", 0) >= 30:
            beta, t = st["beta"], st["t"]
            reading = ("sem alpha detectável" if abs(t) < 2 else
                       ("o modelo antecipa o movimento" if beta > 0 else
                        "o modelo é contrário ao movimento"))
            alpha[market_key] = {"n": st["n"], "beta": round(beta, 4),
                                 "t": round(t, 2), "r2": round(st["r2"], 4),
                                 "reading": reading}
    out["alpha"] = alpha
    # mesma população do relatório (todos os jogos de teste), para não haver
    # dois "rho" diferentes entre relatório e painel de auditoria
    out["correlation"] = corr if corr is not None else estimate_correlation(results)
    return out


# ------------------------------------------------------------------ CLI


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest FutAnalytics v3")
    ap.add_argument("--divs", nargs="+", default=list(FD_LEAGUES.keys()))
    ap.add_argument("--train", nargs="+", default=["1920", "2021", "2122", "2223"])
    ap.add_argument("--test", nargs="+", default=["2324", "2425", "2526"])
    ap.add_argument("--fit-calibration", action="store_true",
                    help="treina as curvas isotônicas nas temporadas de treino")
    ap.add_argument("--calib-file", default=None,
                    help="curvas de calibração específicas (default: app/calibration_data.json)")
    ap.add_argument("--report", default=None)
    ap.add_argument("--json", dest="json_out", default=None,
                    help="grava backtest_summary.json (consumido pela aba Auditoria do app)")
    ap.add_argument("--model-weight", type=float, default=0.25)
    ap.add_argument("--devig", default="power", choices=list(mk.DEVIG_METHODS))
    args = ap.parse_args(argv)

    print("== Carregando dados ==")
    train_matches: list[dict] = []
    test_matches: list[dict] = []
    for season in args.train:
        for div in args.divs:
            train_matches += load_fd_season(div, season)
    for season in args.test:
        for div in args.divs:
            test_matches += load_fd_season(div, season)
    train_matches.sort(key=lambda m: m["date"])
    test_matches.sort(key=lambda m: m["date"])
    print(f"  treino: {len(train_matches)} jogos | teste: {len(test_matches)} jogos")
    if not test_matches:
        print("Nenhum jogo de teste encontrado. Aponte FUTA_DATA_DIR para um "
              "espelho local no formato <temporada>/<DIV>.csv.", file=sys.stderr)
        return 1

    xg_data = load_xg_matches()
    if xg_data:
        n = attach_xg(train_matches, xg_data) + attach_xg(test_matches, xg_data)
        cov = 100.0 * sum(1 for m in test_matches if m.get("xgh") is not None) / len(test_matches)
        print(f"  xG casado em {n} jogos (cobertura no teste: {cov:.0f}%)")
    else:
        cov = 0.0
        print("  sem xG local (FUTA_XG_DIR): rodando no modo conservador de gols")

    all_cal: dict = {}
    if args.fit_calibration:
        print("== Treinando calibração (temporadas de treino) ==")
        for mode in ("joint", "xg", "goals"):
            res = run(train_matches, mode=mode, calibrators=None,
                      model_weight=args.model_weight, devig_method=args.devig)
            cals = {}
            for market_key in calib.DIRECT_MARKETS:
                pairs = [(r["raw"][market_key], outcome(r, market_key), 1.0)
                         for r in res if market_key in r["raw"]]
                if len(pairs) >= 200:
                    cals[market_key] = calib.Calibrator().fit(pairs)
            all_cal[mode] = cals
            print(f"  [{mode}] {len(cals)} curvas, {sum(c.n for c in cals.values()):.0f} observações")
        calib.save_all(all_cal, seasons=list(args.train), divs=list(args.divs),
                       model_weight=args.model_weight, devig=args.devig)
        print(f"  salvo em {calib.CALIB_PATH}")

    print("== Rodando o pipeline no teste ==")
    calib_path = Path(args.calib_file) if args.calib_file else None
    cals = (all_cal.get("joint") if all_cal else None) or calib.load_all(calib_path).get("joint")
    if not all_cal:
        cmeta = calib.load_meta(calib_path)
        if cmeta:
            treinadas = cmeta.get("seasons") or []
            fora = [s for s in args.test if s in treinadas]
            if fora:
                print(f"  AVISO: calibração treinada com temporada(s) do teste {fora} "
                      "— resultados calibrados são vazados", file=sys.stderr)
    results = run(test_matches, mode="joint", calibrators=cals,
                  model_weight=args.model_weight, devig_method=args.devig)
    # adiciona as probabilidades do mercado para o controle
    for r in results:
        r["market"] = market_probs(r, args.devig)
    print(f"  {len(results)} jogos avaliáveis")

    corr = estimate_correlation(test_matches)
    report = [
        "# Relatório de backtest — FutAnalytics v3", "",
        f"Gerado em {dt.date.today().isoformat()} · teste {args.test} · "
        f"treino {args.train} · ligas {', '.join(args.divs)}", "",
        f"*{len(results)} jogos avaliáveis (histórico ≥ {MIN_HIST} jogos) · "
        f"cobertura de xG no teste: {cov:.0f}% · devig: {args.devig} · "
        f"peso do modelo na mistura: {args.model_weight:.0%}*", "",
        "**Como ler este relatório.** ROI medido nas odds de **abertura** da B365 "
        "(o preço que se consegue de verdade antes do fechamento), com intervalo "
        "de confiança de 95% por bootstrap. CLV = quanto a odd pega bateu a odd de "
        "fechamento — o melhor indicador antecedente de vantagem real. Amostra "
        "pequena com IC largo **não** é evidência.", "",
        "---", "",
        "## 1. Poder preditivo", "",
        block_probability(results, "Brier e LogLoss (modelo / calibrado / misturado / mercado)"),
        block_alpha(results),
        block_line_movement(results),
        "## 2. Políticas em dinheiro", "",
        block_strategies(results, cap_pct=3.0),
        block_devig(results),
        "## 3. Correlação entre jogos da mesma rodada", "",
        f"- Pares de jogos analisados: **{corr.get('n', 0)}**",
        f"- P(Over 2.5) individual: **{corr.get('p_over', float('nan')):.3f}** · "
        f"produto das marginais: **{corr.get('indep', float('nan')):.5f}** · "
        f"probabilidade conjunta observada: **{corr.get('joint_obs', float('nan')):.5f}**",
        f"- ρ (phi) implícito: **{corr.get('rho_phi', float('nan')):+.4f}**",
        "",
        "## 4. Limitações (leia antes de usar)", "",
        "- Odds de abertura B365 apenas: não há comparação entre casas nem odds "
        "de mercados de time (ambas marcam sim/não, totais por time).",
        "- A amostra de cada liga é de ~1.400 jogos por temporada; ROI com IC "
        "que cruza zero **não** é lucro demonstrado.",
        "- A calibração é treinada em temporadas passadas e aplicada adiante; "
        "mudanças de regime (regras, estilo de jogo) reduzem sua validade.",
        "- Modelo não vê escalação, lesão, motivação ou clima: EV alto suspeito "
        "deve ser conferido antes de virar aposta.",
        "",
    ]
    text = "\n".join(report)
    print(text[:3000])
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"\nRelatório salvo em {args.report}")
    if args.json_out:
        summary = summarize(results, kelly_mult=0.25, devig=args.devig, corr=corr)
        summary.update({"test": " ".join(args.test), "train": " ".join(args.train),
                        "leagues": list(args.divs), "xg_coverage_pct": round(cov, 1)})
        Path(args.json_out).write_text(json.dumps(summary, indent=1, ensure_ascii=False))
        print(f"Resumo JSON salvo em {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
