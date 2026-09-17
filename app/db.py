"""Persistência local: configurações, apostas, banca, cache e histórico de odds.

SQLite puro (zero dependência) com duas melhorias de v3:

* **WAL** e ``busy_timeout``: o backend é assíncrono e várias análises gravam
  ao mesmo tempo; sem isso, escritas concorrentes davam ``database is locked``.
* **histórico de odds** (``odds_history``): cada vez que o usuário salva o
  preço da sua casa, guardamos uma foto. O movimento da linha (primeiro preço
  vs. último) é sinal de mercado — e o único jeito de medir CLV sem uma API
  paga de odds.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path

# Caminho do banco: por padrão na raiz do projeto (``futanalytics.db``), mas
# ``FUTA_DB`` permite apontar para disco persistente (ou para um banco de teste).
DB_PATH = Path(os.environ.get("FUTA_DB") or Path(__file__).resolve().parent.parent / "futanalytics.db")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT, match_date TEXT, label TEXT, market TEXT, selection TEXT,
                odd REAL, stake REAL, prob REAL, ev REAL, is_multiple INTEGER DEFAULT 0,
                legs TEXT, status TEXT DEFAULT 'open', profit REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, count INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fixture_id TEXT NOT NULL,
                market TEXT NOT NULL,
                odd REAL NOT NULL,
                captured_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_odds_fixture
                ON odds_history(fixture_id, market, captured_at);
            """
        )
        _migrate(c)


def _migrate(c: sqlite3.Connection):
    cols = {r["name"] for r in c.execute("PRAGMA table_info(bets)").fetchall()}
    for name, ddl in (("shadow", "INTEGER DEFAULT 0"), ("closing_odd", "REAL"),
                      ("clv", "REAL"), ("edge", "REAL")):
        if name not in cols:
            c.execute(f"ALTER TABLE bets ADD COLUMN {name} {ddl}")


# ------------------------------------------------------------------ settings/cache


def get_setting(key: str, default=None):
    with conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def cache_get(key: str):
    with conn() as c:
        row = c.execute("SELECT value, expires FROM cache WHERE key=?", (key,)).fetchone()
    if row and row["expires"] > time.time():
        return json.loads(row["value"])
    return None


def cache_set(key: str, value, ttl_seconds: int):
    with conn() as c:
        c.execute("INSERT INTO cache(key,value,expires) VALUES(?,?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
                  (key, json.dumps(value), time.time() + ttl_seconds))


def api_usage_today(day: str) -> int:
    with conn() as c:
        row = c.execute("SELECT count FROM api_usage WHERE day=?", (day,)).fetchone()
    return row["count"] if row else 0


def api_usage_inc(day: str, n: int = 1):
    with conn() as c:
        c.execute("INSERT INTO api_usage(day,count) VALUES(?,?) "
                  "ON CONFLICT(day) DO UPDATE SET count=count+excluded.count", (day, n))


# ------------------------------------------------------------------ odds


def odds_snapshot(fixture_id: str, odds: dict, when: str | None = None):
    """Grava uma foto dos preços (uma linha por mercado)."""
    ts = when or dt.datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        for market, odd in odds.items():
            try:
                c.execute("INSERT INTO odds_history(fixture_id, market, odd, captured_at) "
                          "VALUES(?,?,?,?)", (str(fixture_id), market, float(odd), ts))
            except (ValueError, TypeError, sqlite3.Error):
                continue


def odds_movement(fixture_id: str) -> dict:
    """Movimento de linha por mercado: primeiro preço → último, abertura.

    Linha caindo (odd menor) significa que o mercado comprou aquele lado — se
    você já tinha a odd antiga, isso é CLV acontecendo em tempo real.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT market, odd, captured_at FROM odds_history WHERE fixture_id=? "
            "ORDER BY captured_at ASC, id ASC", (str(fixture_id),)).fetchall()
    first: dict = {}
    last: dict = {}
    n: dict = {}
    for r in rows:
        first.setdefault(r["market"], r["odd"])
        last[r["market"]] = r["odd"]
        n[r["market"]] = n.get(r["market"], 0) + 1
    out = {}
    for market, last_odd in last.items():
        if n[market] < 2:
            continue
        delta = last_odd - first[market]
        out[market] = {
            "first": round(first[market], 3),
            "last": round(last_odd, 3),
            "delta_pct": round(100 * (delta / first[market]), 2) if first[market] else 0.0,
            "direction": "caindo" if delta < 0 else ("subindo" if delta > 0 else "estável"),
            "n": n[market],
        }
    return out
