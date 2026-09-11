"""Forças de ataque/defesa ajustadas por adversário (modelo conjunto por liga).

Por que: o estimador por time (médias ponderadas do próprio time) sofre de
viés de calendário — quem enfrentou defesas fracas parece melhor do que é.
O modelo conjunto resolve isso de uma vez: ajusta UM modelo
    log λ_mandante = μ + vantagem_casa + ataque[mandante] + defesa[visitante]
    log λ_visitante = μ − vantagem_casa + ataque[visitante] + defesa[mandante]
para TODOS os jogos recentes da liga ao mesmo tempo, com regularização ridge
(equivalente a shrinkage bayesiano) e peso decaindo com a idade do jogo.
Ajustado sobre o xG do Understat (não gols brutos) quando disponível.

É regressão de Poisson log-linear — convexa — resolvida por gradiente
descendente full-batch (~90% de qualidade de MLE com zero dependências).

Custo: um ajuste por (liga, semana) — cacheado. No backtest, um ajuste por
(liga, semana) por temporada; nada é reajustado por jogo (sem vazamento:
só jogos ANTERIORES à data entram no ajuste).
"""
from __future__ import annotations

import datetime as dt
import math

DECAY_PER_DAY = math.log(2) / 90.0   # meia-vida de 90 dias (força estrutural)
RIDGE = 8.0                          # penalidade de atk/def (log-units²)
LR = 0.20                            # taxa de aprendizado inicial
EPOCHS = 350
MIN_MATCHES = 25                     # mínimo para o ajuste valer
OLD_SEASON_WEIGHT = 0.35             # peso das partidas de temporadas anteriores

# parâmetros globais de segurança
LAM_MIN, LAM_MAX = 0.15, 4.5


def _weight(days_ago: float, old_season: bool) -> float:
    w = math.exp(-DECAY_PER_DAY * max(days_ago, 0))
    return w * (OLD_SEASON_WEIGHT if old_season else 1.0)


def fit_league(matches: list[dict], on_date: dt.date,
               xg_preferred: bool = True) -> dict | None:
    """Ajusta o modelo da liga com jogos ANTERIORES a on_date.

    matches: [{date, home, away, hg, ag, xgh, xga}] (qualquer ordem).
    Retorna {mu, home_adv, atk{team}, def{team}, n_matches, teams[]} ou None.
    """
    rows = []
    teams: set[str] = set()
    for m in matches:
        d = m["date"]
        if d >= on_date:
            continue  # blindagem contra vazamento
        age = (on_date - d).days
        if age > 420:
            continue
        use_xg = xg_preferred and m.get("xgh") is not None and m.get("xga") is not None
        gh = m["xgh"] if use_xg else m["hg"]
        ga = m["xga"] if use_xg else m["ag"]
        if gh is None or ga is None:
            continue
        w = _weight(age, old_season=d.year < on_date.year)
        if w <= 0.005:
            continue
        rows.append((m["home"], m["away"], float(gh), float(ga), w))
        teams.add(m["home"]); teams.add(m["away"])
    if len(rows) < MIN_MATCHES or len(teams) < 8:
        return None

    teams = sorted(teams)
    base = sum(g * w for _h, _a, g, _ga, w in rows + [(r[0], r[1], r[3], 0, r[4]) for r in rows])
    base /= sum(w * 2 for r in rows)
    mu = math.log(max(base, 0.2))
    home_adv = 0.15
    atk = {t: 0.0 for t in teams}
    deff = {t: 0.0 for t in teams}

    lr = LR
    for _epoch in range(EPOCHS):
        g_mu = g_ha = 0.0
        g_atk = {t: RIDGE * atk[t] for t in teams}
        g_def = {t: RIDGE * deff[t] for t in teams}
        w_sum = 0.0
        for h, a, gh, ga, w in rows:
            lam_h = math.exp(mu + home_adv + atk[h] + deff[a])
            lam_a = math.exp(mu - home_adv + atk[a] + deff[h])
            dh = w * (1.0 - gh / max(lam_h, 1e-9))
            da = w * (1.0 - ga / max(lam_a, 1e-9))
            g_mu += dh + da
            g_ha += dh - da
            g_atk[h] += dh; g_def[a] += dh
            g_atk[a] += da; g_def[h] += da
            w_sum += 2 * w
        step = lr / max(w_sum, 1e-9)
        mu -= step * g_mu
        home_adv -= step * g_ha
        for t in teams:
            atk[t] -= step * g_atk[t]
            deff[t] -= step * g_def[t]
        lr *= 0.995
        # clamp de estabilidade
        home_adv = max(min(home_adv, 1.0), -0.2)
        for t in teams:
            atk[t] = max(min(atk[t], 1.2), -1.2)
            deff[t] = max(min(deff[t], 1.2), -1.2)

    return {
        "mu": mu, "home_adv": home_adv,
        "atk": atk, "def": deff,
        "teams": teams,
        "n_matches": len(rows),
        "using_xg": xg_preferred and any(True for r in rows),
    }


def predict_lambdas(model: dict, home: str, away: str) -> tuple[float, float] | None:
    """λ do mandante e do visitante pelo modelo conjunto."""
    atk, deff = model["atk"], model["def"]
    if home not in atk or away not in atk:
        # time fora da liga no período (ex.: promovido com jogos em outra liga)
        if home not in atk and away not in atk:
            return None
        atk = model["atk"]; deff = model["def"]
    ah = atk.get(home, 0.0); dh = deff.get(home, 0.0)
    aa = atk.get(away, 0.0); da = deff.get(away, 0.0)
    lam_h = math.exp(model["mu"] + model["home_adv"] + ah + da)
    lam_a = math.exp(model["mu"] - model["home_adv"] + aa + dh)
    lam_h = min(max(lam_h, LAM_MIN), LAM_MAX)
    lam_a = min(max(lam_a, LAM_MIN), LAM_MAX)
    return lam_h, lam_a


def league_env(model: dict) -> dict:
    """Contexto de gols da liga segundo o modelo (por 90 min)."""
    return {
        "avg_home": round(math.exp(model["mu"] + model["home_adv"]), 2),
        "avg_away": round(math.exp(model["mu"] - model["home_adv"]), 2),
        "avg_total": round(math.exp(model["mu"] + model["home_adv"]) + math.exp(model["mu"] - model["home_adv"]), 2),
        "n_matches": model["n_matches"],
    }
