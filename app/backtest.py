"""Backtesting nativo do FutAnalytics.

Uso:
    python -m app.backtest --train 2023 2024 --test 2025 --fit-calibration \
        --report backtest_report.md

O que faz:
1. Baixa (e cacheia em data_cache/) temporadas de football-data.co.uk —
   resultados + odds B365 de abertura e FECHAMENTO — e o histórico de xG do
   Understat (mesma fonte usada em produção).
2. Reproduz o pipeline exato do app jogo a jogo, SEM vazamento: o histórico
   de cada time é construido só com jogos anteriores à partida analisada.
3. Avalia: Brier/LogLoss vs. mercado vs. baseline, tabela de calibração,
   teste de alfa (modelo vs. mercado em quintis de divergência) e a estratégia
   do app (min_ev, blend com mercado, Kelly) com ROI e CLV (odd pegada vs.
   fechamento — o melhor preditor de edge de longo prazo conhecido).
4. Com --fit-calibration, treina as curvas isotônicas nas temporadas de
   treino (modos "xg" e "goals") e grava app/calibration_data.json.

Ferramenta offline/síncrona de propósito: roda fora do servidor, sem tocar
no banco do app.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from . import calibration as calib
from . import understat
from .model import (PARAMS, TeamSample, analyze_match, blend_markets,
                    de_vig_markets, kelly_stake, league_priors, league_xg_priors,
                    pick_best_market)

FD_BASE = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"

FD_LEAGUES = {  # div do football-data -> nome interno (chave de UNDERSTAT_LEAGUES)
    "E0": "Premier League",
    "SP1": "La Liga",
    "I1": "Serie A (Itália)",
    "D1": "Bundesliga",
    "F1": "Ligue 1",
}

MIN_HIST = 8          # mínimo de jogos por time para avaliar a partida
TRAIN_DAYS_BACK = 240 # mesma janela do app


# ------------------------------------------------------------------ dados
def _cache_get(key: str):
    f = CACHE_DIR / key
    if f.exists():
        return f.read_text(encoding="utf-8")
    return None


def _cache_put(key: str, text: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / key).write_text(text, encoding="utf-8")


def _fetch(url: str, cache_key: str) -> str | None:
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        req = urllib.request.Request(url, headers={"User-Agent": understat.US_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  ! falha ao baixar {url}: {e}", file=sys.stderr)
        return None
    _cache_put(cache_key, text)
    return text


def _parse_fd_date(s: str) -> dt.date | None:
    try:
        d, m, y = s.split("/")
        y = int(y)
        return dt.date(y + 2000 if y < 100 else y, int(m), int(d))
    except (ValueError, AttributeError):
        return None


def load_fd_matches(divs: list[str], seasons: list[str]) -> list[dict]:
    """Temporadas do football-data.co.uk com odds de abertura e fechamento."""
    out = []
    for season in seasons:
        for div in divs:
            text = _fetch(FD_BASE.format(season=season, div=div), f"fd_{season}_{div}.csv")
            if not text:
                continue
            for row in csv.DictReader(io.StringIO(text)):
                date = _parse_fd_date(row.get("Date", ""))
                if not date:
                    continue
                def f(col):
                    try:
                        v = float(row.get(col) or 0)
                        return v if v > 1.01 else None
                    except (TypeError, ValueError):
                        return None
                try:
                    hg, ag = int(row["FTHG"]), int(row["FTAG"])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({
                    "league": FD_LEAGUES[div], "div": div, "date": date,
                    "home": row["HomeTeam"], "away": row["AwayTeam"],
                    "hg": hg, "ag": ag,
                    "odds": {k: v for k, v in {
                        "home": f("B365H"), "draw": f("B365D"), "away": f("B365A"),
                        "over_2.5": f("B365>2.5"), "under_2.5": f("B365<2.5"),
                    }.items() if v},
                    "closing": {k: v for k, v in {
                        "home": f("B365CH"), "draw": f("B365CD"), "away": f("B365CA"),
                        "over_2.5": f("B365C>2.5"), "under_2.5": f("B365C<2.5"),
                    }.items() if v},
                })
    out.sort(key=lambda m: m["date"])
    return out


def load_understat_matches(internal_leagues: list[str], seasons: list[int]) -> dict:
    """{liga_interna: [match]} com xG por jogo, cacheado em data_cache/."""
    out = {}
    inv = {v: k for k, v in understat.UNDERSTAT_LEAGUES.items()}
    for slug in [understat.UNDERSTAT_LEAGUES[l] for l in internal_leagues
                 if l in understat.UNDERSTAT_LEAGUES]:
        league_name = inv[slug]
        matches = []
        for season in seasons:
            cached = _cache_get(f"us_{slug}_{season}.json")
            if cached is not None:
                try:
                    data = json.loads(cached)
                except ValueError:
                    continue
                if isinstance(data, dict):          # resposta completa da API
                    data = data.get("dates") or []
            else:
                text = _fetch(f"https://understat.com/getLeagueData/{slug}/{season}",
                              f"us_{slug}_{season}.json")
                if not text:
                    continue
                try:
                    data = json.loads(text).get("dates") or []
                except ValueError:
                    continue
            for m in data:
                try:
                    matches.append({
                        "h": m["h"]["title"], "a": m["a"]["title"],
                        "hg": int(m["goals"]["h"]), "ag": int(m["goals"]["a"]),
                        "xgh": float(m["xG"]["h"]), "xga": float(m["xG"]["a"]),
                        "date": dt.datetime.strptime(m["datetime"][:10], "%Y-%m-%d").date(),
                        "isResult": bool(m.get("isResult")),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
        out[league_name] = matches
    return out


def attach_xg(matches: list[dict], us_matches: dict) -> None:
    """Casa resultados do fd com xG do understat por (times resolvidos, data ± 2 dias)."""
    for league, ms in us_matches.items():
        titles = sorted({m["h"] for m in ms} | {m["a"] for m in ms})
        idx = defaultdict(list)
        for m in ms:
            if not m["isResult"]:
                continue
            idx[(m["h"], m["a"])].append(m)
        for m in matches:
            if m["league"] != league:
                continue
            us_h = understat.match_understat_team(m["home"], titles)
            us_a = understat.match_understat_team(m["away"], titles)
            if not us_h or not us_a:
                continue
            best, best_gap = None, 3
            for cand in idx.get((us_h, us_a), ()):
                gap = abs((cand["date"] - m["date"]).days)
                if gap < best_gap:
                    best, best_gap = cand, gap
            if best:
                m["xgh"], m["xga"] = best["xgh"], best["xga"]


# ------------------------------------------------------------------ pipeline
def build_histories(matches: list[dict]):
    """Histórico por time em ordem cronológica (sem vazamento)."""
    hist = defaultdict(list)  # team -> [(date, is_home, gf, ga, xgf, xga)]
    for m in matches:
        xgh = m.get("xgh"); xga = m.get("xga")
        hist[m["home"]].append((m["date"], True, m["hg"], m["ag"], xgh, xga))
        hist[m["away"]].append((m["date"], False, m["hg"], m["ag"], xga, xgh))
    return hist


def team_games(hist, team, today, n=40, use_xg=True):
    games = []
    for date, is_home, gf, ga, xgf, xga in reversed(hist.get(team, [])):
        if date >= today:
            continue  # blindagem extra contra vazamento
        days = (today - date).days
        if days > TRAIN_DAYS_BACK:
            break
        games.append((days, is_home, gf, ga, xgf, xga))
        if len(games) >= n:
            break
    return list(reversed(games))


def run(matches: list[dict], mode: str = "xg", xg_weight: float | None = None,
        calibrators: dict | None = None, model_weight: float = 0.25,
        min_ev: float = 0.03, kelly_uncertainty: bool = True) -> list[dict]:
    """Roda o pipeline do app sobre as partidas. mode: 'xg' | 'goals'."""
    if mode == "goals":
        xg_weight = 0.0
    hist = build_histories(matches)
    results = []
    for m in matches:
        gh = team_games(hist, m["home"], m["date"])
        ga_ = team_games(hist, m["away"], m["date"])
        if len(gh) < MIN_HIST or len(ga_) < MIN_HIST:
            continue
        pool = [g for g in gh + ga_]
        home_avg, away_avg = league_priors(pool)
        xg_home_avg, xg_away_avg = league_xg_priors(pool)

        res = analyze_match(TeamSample(m["home"], gh), TeamSample(m["away"], ga_),
                            home_avg, away_avg, xg_home_avg, xg_away_avg,
                            xg_weight=xg_weight)
        raw_markets = dict(res["markets"])
        cal_markets = dict(raw_markets)
        if calibrators:
            res["markets"] = calib.apply_calibration(res["markets"], calibrators)
            cal_markets = dict(res["markets"])
        if m["odds"]:
            res["markets"] = blend_markets(res["markets"], m["odds"], model_weight)

        best = pick_best_market(res, m["odds"])
        results.append({
            **m, "raw": raw_markets, "cal_probs": cal_markets, "probs": res["markets"],
            "n_eff": (res["sample_home"] + res["sample_away"]) / 2,
            "best": best, "xg_used": res.get("xg_used", False),
        })
    return results


# ------------------------------------------------------------------ métricas
def brier(ps, rs):
    return sum((p - r) ** 2 for p, r in zip(ps, rs)) / len(ps)


def logloss(ps, rs, eps=1e-4):
    return -sum(math.log(max(p if r else 1 - p, eps)) for p, r in zip(ps, rs)) / len(ps)


def _devig2(a, b):
    ia, ib = 1 / a, 1 / b
    s = ia + ib
    return ia / s, ib / s


def _devig3(a, b, c):
    inv = [1 / x for x in (a, b, c)]
    s = sum(inv)
    return [x / s for x in inv]


def outcome(r, market):
    if market == "home":
        return 1.0 if r["hg"] > r["ag"] else 0.0
    if market == "away":
        return 1.0 if r["ag"] > r["hg"] else 0.0
    if market == "draw":
        return 1.0 if r["hg"] == r["ag"] else 0.0
    if market == "over_2.5":
        return 1.0 if r["hg"] + r["ag"] >= 3 else 0.0
    if market == "under_2.5":
        return 1.0 if r["hg"] + r["ag"] <= 2 else 0.0
    if market == "over_1.5":
        return 1.0 if r["hg"] + r["ag"] >= 2 else 0.0
    if market == "over_3.5":
        return 1.0 if r["hg"] + r["ag"] >= 4 else 0.0
    if market == "btts_yes":
        return 1.0 if r["hg"] > 0 and r["ag"] > 0 else 0.0
    raise ValueError(market)


def market_prob(r, market):
    o = r["odds"]
    if market in ("home", "draw", "away") and all(k in o for k in ("home", "draw", "away")):
        return _devig3(o["home"], o["draw"], o["away"])[["home", "draw", "away"].index(market)]
    if market == "over_2.5" and "over_2.5" in o and "under_2.5" in o:
        return _devig2(o["over_2.5"], o["under_2.5"])[0]
    if market == "under_2.5" and "over_2.5" in o and "under_2.5" in o:
        return _devig2(o["over_2.5"], o["under_2.5"])[1]
    return None


def metrics_block(results: list[dict], label: str) -> str:
    """Métricas sobre as probs CALIBRADAS do modelo pré-blend (qualidade pura)."""
    lines = [f"### {label}", "", "| Mercado | Brier modelo | Brier mercado | Brier ingênuo | LogLoss modelo | LogLoss mercado |", "|---|---|---|---|---|---|"]
    for mk in ("over_2.5", "home", "away", "draw"):
        preds, reals, mkt, naive = [], [], [], 0.0
        for r in results:
            mp = market_prob(r, mk)
            if mp is None:
                continue
            preds.append(r["cal_probs"][mk]); reals.append(outcome(r, mk)); mkt.append(mp)
        if not preds:
            continue
        base = sum(reals) / len(reals)
        lines.append(
            f"| {mk} | {brier(preds, reals):.4f} | {brier(mkt, reals):.4f} | "
            f"{brier([base] * len(reals), reals):.4f} | {logloss(preds, reals):.4f} | {logloss(mkt, reals):.4f} |")
    lines.append("")
    # tabela de calibração do over_2.5
    preds = [(r["cal_probs"]["over_2.5"], outcome(r, "over_2.5")) for r in results
             if market_prob(r, "over_2.5") is not None]
    lines.append("**Calibração Over 2.5** (prob. média prevista → frequência real):")
    lines.append("")
    lines.append("| Faixa | Previsto | Real | n | Desvio |")
    lines.append("|---|---|---|---|---|")
    nbins = 5
    bins = [[] for _ in range(nbins)]
    for p, y in preds:
        bins[min(int(p * nbins), nbins - 1)].append((p, y))
    for i, b in enumerate(bins):
        if not b:
            continue
        mp = sum(p for p, _ in b) / len(b)
        fr = sum(y for _, y in b) / len(b)
        lines.append(f"| {i/nbins:.0%}–{(i+1)/nbins:.0%} | {mp:.3f} | {fr:.3f} | {len(b)} | {fr-mp:+.3f} |")
    lines.append("")
    # alpha: quando o modelo discorda do mercado, quem acerta?
    disc = sorted(((r["cal_probs"]["over_2.5"] - market_prob(r, "over_2.5"),
                    outcome(r, "over_2.5"), market_prob(r, "over_2.5"))
                   for r in results if market_prob(r, "over_2.5") is not None),
                  key=lambda t: t[0])
    if disc:
        lines.append("**Teste de alfa (Over 2.5):** ao divergir do mercado, quem tem razão?")
        lines.append("")
        lines.append("| Quintil | Divergência (mod-mkt) | Mercado diz | Real |")
        lines.append("|---|---|---|---|")
        q = max(len(disc) // 5, 1)
        for i in range(5):
            chunk = disc[i * q:(i + 1) * q] if i < 4 else disc[4 * q:]
            if not chunk:
                continue
            dm = sum(c[0] for c in chunk) / len(chunk)
            fr = sum(c[1] for c in chunk) / len(chunk)
            mp = sum(c[2] for c in chunk) / len(chunk)
            lines.append(f"| Q{i+1} | {dm:+.3f} | {mp:.3f} | {fr:.3f} |")
        lines.append("")
    return "\n".join(lines)


def strategy_block(results: list[dict], model_weight: float = 0.25,
                   min_ev: float = 0.03, kelly_uncertainty: bool = True) -> str:
    """Simula a estratégia do app (e variantes) medindo ROI + CLV vs fechamento."""
    from .model import pick_best_market as _pick

    def collect(probs_key, weight):
        bets = []
        for r in results:
            odds = r["odds"]
            if not odds:
                continue
            if probs_key == "blend":
                best = r["best"]
            else:
                # picks do modelo calibrado pré-blend (qualidade do modelo puro)
                analysis = {"markets": r.get("cal_probs", r["raw"]), "confidence": 0.9}
                best = _pick(analysis, odds)
            if not best or best["ev"] is None or best["ev"] * 100 < min_ev:
                continue
            mk = best["market"]
            if not odds.get(mk):
                continue
            bets.append({
                "mk": mk, "p": best["prob"], "odd": odds[mk],
                "odd_close": r["closing"].get(mk), "y": outcome(r, mk),
                "n_eff": r["n_eff"],
            })
        return bets

    def simulate(bets):
        profit_flat = staked = 0.0
        bankroll = 1000.0
        by_market = defaultdict(lambda: [0, 0.0])
        for b in bets:
            won = b["y"] > 0.5
            profit_flat += (b["odd"] - 1) if won else -1
            staked += 1
            ks = kelly_stake(b["p"], b["odd"], bankroll, fraction=0.25, cap_pct=0.03,
                             n_eff=b["n_eff"], uncertainty=kelly_uncertainty)
            bankroll += ks["stake"] * ((b["odd"] - 1) if won else -1)
            by_market[b["mk"]][0] += 1
            by_market[b["mk"]][1] += (b["odd"] - 1) if won else -1
        roi = 100 * profit_flat / staked if staked else 0.0
        roi_k = 100 * (bankroll - 1000) / 1000 if bets else 0.0
        clvs = [(b["odd"] / b["odd_close"] - 1) * 100 for b in bets if b["odd_close"]]
        avg_clv = sum(clvs) / len(clvs) if clvs else None
        beat = 100 * sum(1 for c in clvs if c > 0) / len(clvs) if clvs else None
        return roi, roi_k, by_market, avg_clv, beat, len(clvs)

    lines = [f"### Estratégia (blend modelo {model_weight:.0%}, min_ev {min_ev:.0%}, "
             f"Kelly {'c/ desconto de incerteza' if kelly_uncertainty else 'sem desconto'})", ""]

    for label, key in ((f"PRODUÇÃO — modelo calibrado + mercado ({model_weight:.0%})", "blend"),
                       ("SÓ MODELO — calibrado, sem mistura com mercado", "raw")):
        bets = collect(key, model_weight)
        if not bets:
            lines.append(f"**{label}:** nenhuma aposta passou o filtro de EV "
                         f"(disciplina: sem valor detectado, sem aposta).")
            lines.append("")
            continue
        roi, roi_k, by_mk, avg_clv, beat, n_clv = simulate(bets)
        lines.append(f"**{label}:** {len(bets)} apostas · ROI flat **{roi:+.2f}%** · ROI Kelly **{roi_k:+.2f}%**")
        lines.append("")
        lines.append("| Mercado | Apostas | ROI flat |")
        lines.append("|---|---|---|")
        for mk, (n, pf) in sorted(by_mk.items(), key=lambda kv: -kv[1][0]):
            lines.append(f"| {mk} | {n} | {100 * pf / n:+.2f}% |")
        if avg_clv is not None:
            lines.append("")
            lines.append(f"CLV vs. fechamento B365 ({n_clv} apostas): média **{avg_clv:+.2f}%** · "
                         f"bateu o fechamento em **{beat:.1f}%**")
        lines.append("")

    # baseline: todo over 2.5 (detecta viés sistemático da liga/odd)
    ov = [r for r in results if r["odds"].get("over_2.5")]
    if ov:
        pf = sum((r["odds"]["over_2.5"] - 1) if r["hg"] + r["ag"] >= 3 else -1 for r in ov)
        lines.append(f"**Baseline (todo Over 2.5 na B365):** {len(ov)} apostas · ROI flat **{100 * pf / len(ov):+.2f}%**")
        lines.append("")
    return "\n".join(lines) + "\n"


def fit_calibration(results: list[dict], mode: str) -> dict:
    """Treina curvas isotônicas por mercado a partir dos probs do modelo."""
    cal = {}
    for mk in calib.DIRECT_MARKETS:
        pairs = []
        for r in results:
            p = r["raw"].get(mk)
            if p is None:
                continue
            pairs.append((p, outcome(r, mk), 1.0))
        if len(pairs) >= 200:
            cal[mk] = calib.Calibrator().fit(pairs)
    return cal


# ------------------------------------------------------------------ CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest FutAnalytics")
    ap.add_argument("--divs", nargs="+", default=["E0", "SP1", "I1", "D1", "F1"])
    ap.add_argument("--train", nargs="+", default=["2324", "2425"])
    ap.add_argument("--test", nargs="+", default=["2526"])
    ap.add_argument("--fit-calibration", action="store_true")
    ap.add_argument("--report", default=None)
    ap.add_argument("--min-ev", type=float, default=0.03)
    ap.add_argument("--model-weight", type=float, default=0.25)
    args = ap.parse_args(argv)

    # 2324 -> 2023; 2526 -> 2025
    def to_us(seasons):
        return [2000 + int(s[:2]) for s in seasons]

    print("== Baixando dados (cache em data_cache/) ==")
    train_matches = load_fd_matches(args.divs, args.train)
    test_matches = load_fd_matches(args.divs, args.test)
    print(f"  treino: {len(train_matches)} jogos ({args.train}) | teste: {len(test_matches)} jogos ({args.test})")

    us_all = load_understat_matches(list(FD_LEAGUES.values()),
                                    sorted(set(to_us(args.train) + to_us(args.test))))
    for name, ms in us_all.items():
        n = sum(1 for m in ms if m["isResult"])
        print(f"  understat {name}: {n} jogos com xG")
    attach_xg(train_matches, us_all)
    attach_xg(test_matches, us_all)
    cov = 100 * sum(1 for m in test_matches if m.get("xgh") is not None) / max(len(test_matches), 1)
    print(f"  cobertura xG no teste: {cov:.0f}%")

    all_cal = {}
    if args.fit_calibration:
        print("\n== Treinando calibração (temporadas de treino) ==")
        for mode, xgw in (("xg", PARAMS["XG_WEIGHT"]), ("goals", 0.0)):
            res_train = run(train_matches, mode=mode, xg_weight=xgw, calibrators=None,
                            model_weight=args.model_weight)
            cals = fit_calibration(res_train, mode)
            all_cal[mode] = cals
            for mk, c in cals.items():
                print(f"  [{mode}] {mk}: {len(c.curve)} blocos, n={c.n:.0f}")
        calib.save_all(all_cal)
        print(f"  salvo em {calib.CALIB_PATH}")

    report = ["# Relatório de backtest — FutAnalytics v2", "",
              f"Gerado em {dt.date.today().isoformat()} · treino {args.train} · teste {args.test} "
              f"({', '.join(args.divs)})", "",
              "---", ""]
    sections = []
    for mode, xgw, label in (("goals", 0.0, "v1: só gols, sem calibração"),
                             ("goals", 0.0, "v1 + calibração"),
                             ("xg", PARAMS["XG_WEIGHT"], "v2: xG sem calibração"),
                             ("xg", PARAMS["XG_WEIGHT"], "v2 completa: xG + calibração")):
        cals = None
        if "calibração" in label:
            cals = all_cal.get(mode) or calib.load_all().get(mode)
        res_test = run(test_matches, mode=mode, xg_weight=xgw, calibrators=cals,
                       model_weight=args.model_weight, min_ev=args.min_ev)
        n_eval = len(res_test)
        print(f"\n== {label} ==  ({n_eval} jogos avaliáveis no teste)")
        sec = [f"## {label}", "", f"*{n_eval} jogos avaliáveis (histórico ≥ {MIN_HIST} jogos)*", ""]
        sec.append(metrics_block(res_test, f"Métricas preditivas — {label}"))
        sec.append(strategy_block(res_test, args.model_weight, args.min_ev))
        sections.append("\n".join(sec))

    # seção extra: produção v2 com min_ev 1% (mais volume, ainda disciplinado)
    cals = all_cal.get("xg") or calib.load_all().get("xg")
    res_lo = run(test_matches, mode="xg", xg_weight=PARAMS["XG_WEIGHT"], calibrators=cals,
                 model_weight=args.model_weight, min_ev=0.01)
    sec = ["## v2 completa · produção com min_ev 1% (mais volume)", ""]
    sec.append(strategy_block(res_lo, args.model_weight, 0.01))
    sections.append("\n".join(sec))

    report.extend(sections)
    report_text = "\n".join(report)
    print("\n" + "=" * 60)
    print(report_text[:4000])
    if args.report:
        Path(args.report).write_text(report_text, encoding="utf-8")
        print(f"\nRelatório completo salvo em {args.report}")


if __name__ == "__main__":
    main()
