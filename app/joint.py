"""Forças de ataque/defesa ajustadas por adversário (modelo conjunto por liga).

**O problema que isto resolve.** O estimador por time (médias ponderadas do
próprio time) sofre de viés de calendário: quem enfrentou defesas fracas
parece melhor do que é. O modelo conjunto ajusta UM modelo para toda a liga::

    log λ_mandante  = μ + h + ataque[mandante] + defesa[visitante]
    log λ_visitante = μ − h + ataque[visitante] + defesa[mandante]

com regularização ridge (shrinkage bayesiano) e peso decaindo com a idade do
jogo (meia-vida de 90 dias). Quando existe xG do Understat, o ajuste é feito
sobre **xG** em vez de gols brutos — menos ruído, menos sorte.

**Como é ajustado.** Regressão de Poisson log-linear (convexa), resolvida por
mínimos quadrados reponderados iterativamente (IRLS) esparsos: como cada jogo
usa só 4 das 2T+2 coordenadas, a matriz de informação é montada em tempo
proporcional ao número de jogos (nada de dependências externas). O avanço usa
o *score* (y − μ) ponderado, com o hessiano exato do Poisson — converge em
poucas dezenas de iterações.

**Blindagem contra vazamento.** ``fit_league`` só usa jogos com data
ESTRITAMENTE anterior a ``on_date``; é o mesmo mecanismo usado no backtest,
onde o ajuste é refeito por (liga, semana) — nunca por jogo, e nunca com
informação do futuro.
"""
from __future__ import annotations

import datetime as dt
import math

DECAY_PER_DAY = math.log(2) / 90.0   # meia-vida de 90 dias (força estrutural)
RIDGE = 8.0                          # penalidade de atk/def (log-units²)
RIDGE_INTERCEPT = 1e-6               # intercepto praticamente livre
ITERS = 40                           # iterações máximas do IRLS
TOL = 1e-6                           # tolerância de convergência (log-λ)
MIN_MATCHES = 25                     # mínimo para o ajuste valer
MAX_AGE_DAYS = 420                   # jogo mais antigo que ainda entra
OLD_SEASON_WEIGHT = 0.35             # peso das partidas de temporadas anteriores

# parâmetros globais de segurança
LAM_MIN, LAM_MAX = 0.15, 4.5


def _weight(days_ago: float, old_season: bool) -> float:
    w = math.exp(-DECAY_PER_DAY * max(days_ago, 0))
    return w * (OLD_SEASON_WEIGHT if old_season else 1.0)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Eliminação de Gauss com pivô parcial (sistema pequeno: 2T+2)."""
    n = len(b)
    m = [[*row[:], b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            continue                      # sistema singular: deixa a coordenada em 0
        if piv != col:
            m[col], m[piv] = m[piv], m[col]
        piv_val = m[col][col]
        for r in range(col + 1, n):
            factor = m[r][col] / piv_val
            if factor:
                for c in range(col, n + 1):
                    m[r][c] -= factor * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(m[i][i]) < 1e-12:
            x[i] = 0.0
            continue
        s = m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / m[i][i]
    return x


def _design(rows: list[tuple], teams: list[str]) -> tuple[list[tuple], list[float], list[float]]:
    """Desenho esparso: cada linha tem só 4 coordenadas (±1).

    ``rows``: ``(home, away, gh, ga, peso, temporada_antiga)``.
    Coordenadas: 0 = intercepto, 1 = vantagem de casa, 2+T = ataque, 3+T = defesa.
    """
    idx = {team: i for i, team in enumerate(teams)}
    n = len(teams)
    out: list[tuple] = []
    y: list[float] = []
    w: list[float] = []
    for home, away, gh, ga, wt, _old in rows:
        ih, ia = idx[home], idx[away]
        out.append(((0, 1.0), (1, 1.0), (2 + ih, 1.0), (2 + n + ia, 1.0)))
        y.append(gh)
        w.append(wt)
        out.append(((0, 1.0), (1, -1.0), (2 + ia, 1.0), (2 + n + ih, 1.0)))
        y.append(ga)
        w.append(wt)
    return out, y, w


def fit_league(matches: list[dict], on_date: dt.date,
               xg_preferred: bool = True, ridge: float = RIDGE) -> dict | None:
    """Ajusta o modelo da liga com jogos ANTERIORES a ``on_date``.

    ``matches``: ``[{date, home, away, hg, ag, xgh, xga}]`` (qualquer ordem).
    Retorna ``{mu, home_adv, atk{time}, def{time}, n_matches, teams, using_xg,
    converged, iters}`` ou ``None`` se não houver amostra suficiente.
    """
    rows: list[tuple] = []
    teams: set[str] = set()
    used_xg = False
    for m in matches:
        d = m["date"]
        if d >= on_date:
            continue                      # blindagem contra vazamento
        age = (on_date - d).days
        if age > MAX_AGE_DAYS:
            continue
        use_xg = xg_preferred and m.get("xgh") is not None and m.get("xga") is not None
        gh = m["xgh"] if use_xg else m["hg"]
        ga = m["xga"] if use_xg else m["ag"]
        if gh is None or ga is None:
            continue
        used_xg = used_xg or use_xg
        w = _weight(age, old_season=d.year < on_date.year)
        if w <= 0.005:
            continue
        rows.append((m["home"], m["away"], float(gh), float(ga), w,
                     d.year < on_date.year))
        teams.add(m["home"])
        teams.add(m["away"])
    if len(rows) < MIN_MATCHES or len(teams) < 8:
        return None

    teams = sorted(teams)
    X, y, w = _design(rows, teams)
    t = len(teams)
    dim = 2 * t + 2

    # inicialização: log da taxa média de gols + pequena vantagem de casa
    tot_w = sum(w)
    base_rate = sum(yi * wi for yi, wi in zip(y, w, strict=False)) / max(tot_w, 1e-9)
    theta = [math.log(max(base_rate, 0.2))] + [0.0] * (dim - 1)
    theta[1] = 0.1

    lam_diag = [RIDGE_INTERCEPT, RIDGE_INTERCEPT] + [ridge] * (2 * t)
    converged = False
    iters = 0
    for iters in range(1, ITERS + 1):  # noqa: B007 — iters é lido depois do laço (diagnóstico)
        a = [[0.0] * dim for _ in range(dim)]
        b = [0.0] * dim
        for idxs, yi, wi in zip(X, y, w, strict=False):
            eta = theta[0] + sum(v * theta[i] for i, v in idxs[1:])
            mu = math.exp(min(max(eta, -12.0), 6.0))
            wz = wi * mu
            for i, vi in idxs:
                b[i] += wi * (yi - mu) * vi
                ai = a[i]
                for j, vj in idxs:
                    ai[j] += wz * vi * vj
        for i in range(dim):
            a[i][i] += lam_diag[i]
        new_theta = [theta[i] + step for i, step in enumerate(_solve(a, b))]
        delta = max(abs(new_theta[i] - theta[i]) for i in range(dim))
        theta = new_theta
        if delta < TOL:
            converged = True
            break

    # centragem: ataque e defesa com média zero (identificabilidade do modelo)
    mu0 = theta[0]
    home_adv = theta[1]
    atk_vals = theta[2:2 + t]
    def_vals = theta[2 + t:2 + 2 * t]
    mean_atk = sum(atk_vals) / t
    mean_def = sum(def_vals) / t
    atk = {team: atk_vals[i] - mean_atk for i, team in enumerate(teams)}
    dfn = {team: def_vals[i] - mean_def for i, team in enumerate(teams)}
    return {
        "mu": mu0 + mean_atk + mean_def,
        "home_adv": home_adv,
        "atk": atk,
        "def": dfn,
        "n_matches": len(rows),
        "teams": teams,
        "using_xg": used_xg,
        "converged": converged,
        "iters": iters,
    }


def predict_lambdas(model: dict, home: str, away: str) -> tuple[float, float] | None:
    """λ do mandante e do visitante pelo modelo conjunto."""
    if not model:
        return None
    atk, dfn = model["atk"], model["def"]
    if home not in atk and away not in atk:
        return None
    ah = atk.get(home, 0.0)
    dh = dfn.get(home, 0.0)
    aa = atk.get(away, 0.0)
    da = dfn.get(away, 0.0)
    lam_h = math.exp(model["mu"] + model["home_adv"] + ah + da)
    lam_a = math.exp(model["mu"] - model["home_adv"] + aa + dh)
    return (min(max(lam_h, LAM_MIN), LAM_MAX),
            min(max(lam_a, LAM_MIN), LAM_MAX))


def league_env(model: dict) -> dict:
    """Contexto de gols da liga segundo o modelo (por 90 min)."""
    host = math.exp(model["mu"] + model["home_adv"])
    away = math.exp(model["mu"] - model["home_adv"])
    return {
        "avg_home": round(host, 2),
        "avg_away": round(away, 2),
        "avg_total": round(host + away, 2),
        "n_matches": model["n_matches"],
    }


def ratings_quality(model: dict) -> dict:
    """Diagnóstico do ajuste: convergência, amostra e dispersão das forças."""
    if not model:
        return {}
    devs = sorted((abs(v) for v in model["atk"].values()), reverse=True)
    return {
        "converged": model.get("converged"),
        "iters": model.get("iters"),
        "n_matches": model["n_matches"],
        "using_xg": model.get("using_xg"),
        "spread": round(devs[0], 3) if devs else 0.0,
    }
