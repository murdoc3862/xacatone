"""SQLite: пользователи и журнал событий (для аналитики и панели состояния)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from .config import ROOT

DB_PATH = ROOT / "data" / "bot.db"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _exec(sql: str, params: tuple = ()) -> None:
    with closing(_connect()) as con, con:
        con.execute(sql, params)


def _all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with closing(_connect()) as con:
        return con.execute(sql, params).fetchall()


def init() -> None:
    with closing(_connect()) as con, con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id         INTEGER PRIMARY KEY,
                name          TEXT,
                username      TEXT,
                first_name    TEXT,
                last_name     TEXT,
                started_at    TEXT NOT NULL,
                registered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT NOT NULL,
                tg_id   INTEGER,
                type    TEXT NOT NULL,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            """
        )


def touch_user(tg_id: int, username, first_name, last_name) -> None:
    _exec(
        """INSERT INTO users (tg_id, username, first_name, last_name, started_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(tg_id) DO UPDATE SET
             username=excluded.username,
             first_name=excluded.first_name,
             last_name=excluded.last_name""",
        (tg_id, username, first_name, last_name, now()),
    )


def get_user(tg_id: int):
    rows = _all("SELECT * FROM users WHERE tg_id=?", (tg_id,))
    return rows[0] if rows else None


def set_name(tg_id: int, name: str) -> None:
    _exec("UPDATE users SET name=?, registered_at=? WHERE tg_id=?", (name, now(), tg_id))


def list_users() -> list[sqlite3.Row]:
    return _all("SELECT * FROM users ORDER BY COALESCE(registered_at, started_at) DESC")


def log_event(tg_id, type_: str, details: str | None = None) -> None:
    _exec("INSERT INTO events (ts, tg_id, type, details) VALUES (?, ?, ?, ?)",
          (now(), tg_id, type_, details))


def last_events(limit: int = 15) -> list[sqlite3.Row]:
    return _all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


def ping() -> bool:
    _all("SELECT 1")
    return True


def stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    day_ago = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    q = lambda sql, p=(): _all(sql, p)[0][0]
    return {
        "started": q("SELECT COUNT(*) FROM users"),
        "registered": q("SELECT COUNT(*) FROM users WHERE name IS NOT NULL"),
        "registered_today": q("SELECT COUNT(*) FROM users WHERE registered_at >= ?", (today,)),
        "errors_24h": q("SELECT COUNT(*) FROM events WHERE type='error' AND ts >= ?", (day_ago,)),
    }
