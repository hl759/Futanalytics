"""Persistência local do SigmaDesk: configurações, trades, capital e cache de API.

Herdeiro direto do `db.py` do FutAnalytics — mesmas três decisões de projeto que
sobrevivem intactas porque continuam corretas:

1. AUTO-REPARO DE SCHEMA. O disco do Render free é volátil: o arquivo pode
   nascer, sumir ou ser criado por fora. Sem o schema, TODA a API respondia 500
   ('no such table'). `conn()` verifica e recria.
2. PURGA DE CACHE NA INICIALIZAÇÃO. O único dado não-volátil escrito
   automaticamente é cache temporário com TTL. Trades NUNCA são gravados sozinhos
   — entra só o que o usuário clica para registrar.
3. MIGRAÇÕES LEVES por `ALTER TABLE`, preservando histórico antigo.

Mudança de vocabulário da migração: `bets` -> `trades`, `odd` -> `entry_iv`,
`closing_odd` -> `closing_iv`, `clv` -> `cvl` (Closing Vol Line: IV de entrada
vs IV no fechamento/expiração — o análogo exato do CLV e, como ele, o melhor
preditor de edge de longo prazo).
"""
import json
import os
import sqlite3
import time
from pathlib import Path

# Override por env para que o smoke test rode contra um banco descartável em vez
# de sujar o real. Útil também no Render, se um dia o disco persistente mudar.
DB_PATH = Path(os.getenv("SIGMADESK_DB")
               or (Path(__file__).resolve().parent.parent / "sigmadesk.db"))

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        value TEXT,
        expires REAL
    );
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        opened_at TEXT,
        ccy TEXT,
        structure TEXT,
        direction TEXT,
        legs TEXT,
        entry_iv REAL,
        premium REAL,
        notional REAL,
        contracts REAL,
        vega REAL,
        delta REAL,
        max_loss_stress REAL,
        prob_edge REAL,
        ev REAL,
        grade TEXT,
        check_score REAL,
        bankroll_at_entry REAL,
        stake_pct REAL,
        expiry TEXT,
        status TEXT DEFAULT 'open',
        pnl REAL DEFAULT 0,
        closing_iv REAL,
        cvl REAL,
        notes TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS api_usage (
        day TEXT PRIMARY KEY,
        count INTEGER DEFAULT 0
    );
""";


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
    ).fetchone()
    if row is None:
        c.executescript(_SCHEMA)
        _migrate(c)
    return c


def init():
    with conn() as c:
        c.executescript(_SCHEMA)
        _migrate(c)


def _migrate(c):
    """Adiciona colunas novas preservando dados antigos."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(trades)").fetchall()}
    for col, decl in (("closing_iv", "REAL"), ("cvl", "REAL"),
                      ("max_loss_stress", "REAL"), ("notes", "TEXT DEFAULT ''")):
        if col not in cols:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} {decl}")
    purge_expired_cache(c)


def purge_expired_cache(c=None):
    own = c is None
    if own:
        c = conn()
    try:
        c.execute("DELETE FROM cache WHERE expires < ?", (time.time(),))
        c.commit()
    finally:
        if own:
            c.close()


def get_setting(key: str, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def cache_get(key: str):
    with conn() as c:
        row = c.execute(
            "SELECT value, expires FROM cache WHERE key=?", (key,)
        ).fetchone()
    if row and row["expires"] > time.time():
        try:
            return json.loads(row["value"])
        except ValueError:
            return None
    return None


def cache_set(key: str, value, ttl_seconds: int):
    with conn() as c:
        c.execute(
            "INSERT INTO cache(key,value,expires) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )


def cache_clear(prefix: str | None = None) -> int:
    """Invalida cache por prefixo (ou tudo, se prefix=None). Devolve qtas linhas.

    Necessário porque mudar `currencies`, `candle_days` ou `provider_force`
    invalida as leituras em cache: sem isso o painel continua servindo a
    superfície da configuração ANTIGA até o TTL vencer, e o usuário vê número de
    um universo que ele acabou de trocar.
    """
    with conn() as c:
        if prefix:
            cur = c.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))
        else:
            cur = c.execute("DELETE FROM cache")
        return cur.rowcount or 0


def api_usage_today(day: str) -> int:
    with conn() as c:
        row = c.execute("SELECT count FROM api_usage WHERE day=?", (day,)).fetchone()
    return row["count"] if row else 0


def api_usage_inc(day: str, n: int = 1):
    with conn() as c:
        c.execute(
            "INSERT INTO api_usage(day,count) VALUES(?,?) "
            "ON CONFLICT(day) DO UPDATE SET count=count+excluded.count",
            (day, n),
        )


# ------------------------------------------------------------------ trades
TRADE_COLUMNS = ("opened_at", "ccy", "structure", "direction", "legs", "entry_iv",
                 "premium", "notional", "contracts", "vega", "delta",
                 "max_loss_stress", "prob_edge", "ev", "grade", "check_score",
                 "bankroll_at_entry", "stake_pct", "expiry", "notes")


def add_trade(t: dict) -> int:
    """Insere um trade. BUG PEGO NO BOOT DA ETAPA 0: `sqlite3.OperationalError:
    21 values for 20 columns`.

    `created_at` já entra como LITERAL (`datetime('now')`) na lista de colunas E
    no VALUES, então ele não pode gerar placeholder. O código antigo usava
    `len(cols) + 1` placeholders — o `+1` contava `created_at` duas vezes, e como
    `cols` só inclui campos presentes em `t`, o descompasso era de exatamente 1:
    N+1 colunas contra N+2 valores. Nenhum INSERT de trade jamais funcionou; só
    apareceu quando o endpoint foi chamado de verdade.

    Invariante que fica: len(colunas) == 1 + len(cols) == 1 + len(vals) + 1(literal).
    """
    cols = [c for c in TRADE_COLUMNS if c in t]
    vals = [json.dumps(t[c]) if c == "legs" else t[c] for c in cols]
    if not cols:                                    # nunca emitir VALUES(...,)
        with conn() as c:
            return int(c.execute(
                "INSERT INTO trades(created_at) VALUES(datetime('now'))").lastrowid)
    placeholders = ",".join("?" * len(cols))
    with conn() as c:
        cur = c.execute(
            f"INSERT INTO trades(created_at,{','.join(cols)}) "
            f"VALUES(datetime('now'),{placeholders})", vals)
        return int(cur.lastrowid)


def list_trades(status: str | None = None) -> list[dict]:
    q = "SELECT * FROM trades"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY id DESC"
    with conn() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    for r in rows:
        try:
            r["legs"] = json.loads(r.get("legs") or "[]")
        except ValueError:
            r["legs"] = []
    return rows


def get_trade(trade_id: int) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["legs"] = json.loads(d.get("legs") or "[]")
    except ValueError:
        d["legs"] = []
    return d


def update_trade(trade_id: int, fields: dict) -> bool:
    allowed = {"status", "pnl", "closing_iv", "cvl", "notes"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return False
    cols = ",".join(f"{k}=?" for k in sets)
    with conn() as c:
        cur = c.execute(f"UPDATE trades SET {cols} WHERE id=?",
                        (*sets.values(), trade_id))
        return cur.rowcount > 0


def delete_trade(trade_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM trades WHERE id=?", (trade_id,))
        return cur.rowcount > 0


def stats() -> dict:
    """Resumo do diário — inclui CVL, que é a métrica que importa."""
    with conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT status,pnl,cvl,entry_iv,closing_iv,grade FROM trades").fetchall()]
    closed = [r for r in rows if r["status"] in ("closed", "expired")]
    wins = [r for r in closed if (r["pnl"] or 0) > 0]
    cvls = [r["cvl"] for r in rows if r["cvl"] is not None]
    grades = {}
    for r in rows:
        g = r.get("grade") or "?"
        grades[g] = grades.get(g, 0) + 1
    return {
        "total": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "closed": len(closed),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "pnl_total": round(sum(r["pnl"] or 0 for r in closed), 4),
        "cvl_mean": round(sum(cvls) / len(cvls), 4) if cvls else None,
        "cvl_beat_rate": (round(sum(1 for v in cvls if v > 0) / len(cvls), 4)
                          if cvls else None),
        "by_grade": grades,
    }


def export_all() -> dict:
    with conn() as c:
        settings = {r["key"]: r["value"]
                    for r in c.execute("SELECT key,value FROM settings").fetchall()}
        trades = [dict(r) for r in c.execute("SELECT * FROM trades").fetchall()]
    return {"version": 1, "app": "SigmaDesk", "exported_at": time.time(),
            "settings": settings, "trades": trades}


def import_all(payload: dict) -> dict:
    """Restaura backup no disco efêmero do Render free."""
    settings = payload.get("settings") or {}
    trades = payload.get("trades") or []
    for k, v in settings.items():
        set_setting(k, v)
    n = 0
    with conn() as c:
        for t in trades:
            if not isinstance(t, dict):
                continue
            cols = [k for k in t if k != "id"]
            if not cols:
                continue
            ph = ",".join("?" * len(cols))
            c.execute(f"INSERT INTO trades({','.join(cols)}) VALUES({ph})",
                      [t[k] for k in cols])
            n += 1
    return {"settings": len(settings), "trades": n}
