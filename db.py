"""Persistência local: configurações, apostas, banca e cache de API."""
import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "futanalytics.db"


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT,
                expires REAL
            );
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                match_date TEXT,
                label TEXT,
                market TEXT,
                selection TEXT,
                odd REAL,
                stake REAL,
                prob REAL,
                ev REAL,
                is_multiple INTEGER DEFAULT 0,
                legs TEXT,
                status TEXT DEFAULT 'open',
                profit REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS api_usage (
                day TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            );
            """
        )


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
        return json.loads(row["value"])
    return None


def cache_set(key: str, value, ttl_seconds: int):
    with conn() as c:
        c.execute(
            "INSERT INTO cache(key,value,expires) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires=excluded.expires",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )


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
