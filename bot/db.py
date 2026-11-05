"""SQLite: пользователи и журнал событий (для аналитики и панели состояния)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from .config import ROOT

DB_PATH = ROOT / "data" / "bot.db"


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_ms() -> str:
    return datetime.now().isoformat(sep=" ", timespec="milliseconds")


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

            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id      INTEGER NOT NULL,
                direction  TEXT NOT NULL,              -- in (от подписчика) | out (от админа)
                text       TEXT,
                media_type TEXT,                       -- photo, voice, document, sticker...
                file_id    TEXT,
                ts         TEXT NOT NULL,
                is_read    INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(tg_id, id);

            CREATE TABLE IF NOT EXISTS quiz_runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id    INTEGER NOT NULL,
                status     TEXT NOT NULL,          -- active | stopped
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                sent_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS quiz_participants (
                run_id      INTEGER NOT NULL,
                tg_id       INTEGER NOT NULL,
                current_q   INTEGER NOT NULL DEFAULT 0,   -- сколько вопросов уже отвечено
                score       INTEGER NOT NULL DEFAULT 0,
                joined_at   TEXT NOT NULL,
                finished_at TEXT,
                last_answer_at TEXT,
                delivered   INTEGER NOT NULL DEFAULT 1,
                place       INTEGER,
                PRIMARY KEY (run_id, tg_id)
            );
            CREATE TABLE IF NOT EXISTS quiz_answers (
                run_id      INTEGER NOT NULL,
                tg_id       INTEGER NOT NULL,
                q_index     INTEGER NOT NULL,
                chosen      INTEGER NOT NULL,
                is_correct  INTEGER NOT NULL,
                answered_at TEXT NOT NULL,
                PRIMARY KEY (run_id, tg_id, q_index)
            );
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


def registered_users() -> list[sqlite3.Row]:
    return _all("SELECT * FROM users WHERE name IS NOT NULL ORDER BY registered_at")


def user_quiz_totals() -> dict[int, dict]:
    rows = _all("""SELECT tg_id, COUNT(*) AS runs, SUM(score) AS score, SUM(current_q) AS answered
                   FROM quiz_participants WHERE current_q > 0 GROUP BY tg_id""")
    return {r["tg_id"]: dict(r) for r in rows}


# ---------- Тесты ----------

def active_run():
    rows = _all("SELECT * FROM quiz_runs WHERE status='active' ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None


def get_run(run_id: int):
    rows = _all("SELECT * FROM quiz_runs WHERE id=?", (run_id,))
    return rows[0] if rows else None


def create_run(quiz_id: int) -> int:
    with closing(_connect()) as con, con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM quiz_runs WHERE status='active'").fetchone():
            raise RuntimeError("Уже идёт другой тест — сначала остановите его")
        cur = con.execute("INSERT INTO quiz_runs (quiz_id, status, started_at) VALUES (?, 'active', ?)",
                          (quiz_id, now_ms()))
        return cur.lastrowid


def add_participant(run_id: int, tg_id: int) -> bool:
    """True, если участник добавлен впервые."""
    with closing(_connect()) as con, con:
        cur = con.execute("INSERT OR IGNORE INTO quiz_participants (run_id, tg_id, joined_at) VALUES (?, ?, ?)",
                          (run_id, tg_id, now_ms()))
        return cur.rowcount == 1


def mark_delivery(run_id: int, tg_id: int, ok: bool) -> None:
    col = "sent_count" if ok else "fail_count"
    with closing(_connect()) as con, con:
        con.execute(f"UPDATE quiz_runs SET {col} = {col} + 1 WHERE id=?", (run_id,))
        if not ok:
            con.execute("UPDATE quiz_participants SET delivered=0 WHERE run_id=? AND tg_id=?", (run_id, tg_id))


def get_participant(run_id: int, tg_id: int):
    rows = _all("SELECT * FROM quiz_participants WHERE run_id=? AND tg_id=?", (run_id, tg_id))
    return rows[0] if rows else None


def record_answer(run_id: int, tg_id: int, q_index: int, chosen: int, is_correct: bool, total: int) -> bool:
    """Атомарно засчитывает ответ. False — если тест не активен или на вопрос уже ответили."""
    ts = now_ms()
    with closing(_connect()) as con, con:
        cur = con.execute(
            """UPDATE quiz_participants
               SET current_q = current_q + 1,
                   score = score + ?,
                   last_answer_at = ?,
                   finished_at = CASE WHEN current_q + 1 >= ? THEN ? ELSE finished_at END
               WHERE run_id=? AND tg_id=? AND current_q=?
                 AND EXISTS (SELECT 1 FROM quiz_runs WHERE id=? AND status='active')""",
            (int(is_correct), ts, total, ts, run_id, tg_id, q_index, run_id))
        if cur.rowcount != 1:
            return False
        con.execute("INSERT INTO quiz_answers VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, tg_id, q_index, chosen, int(is_correct), ts))
        return True


def run_participants(run_id: int) -> list[sqlite3.Row]:
    return _all("""SELECT p.*, u.name, u.username,
                          (julianday(COALESCE(p.finished_at, p.last_answer_at)) - julianday(r.started_at)) * 86400 AS seconds
                   FROM quiz_participants p
                   JOIN quiz_runs r ON r.id = p.run_id
                   LEFT JOIN users u ON u.tg_id = p.tg_id
                   WHERE p.run_id=?""", (run_id,))


def ranking(run_id: int) -> list[sqlite3.Row]:
    """Рейтинг: больше правильных → больше отвеченных → быстрее."""
    ps = [p for p in run_participants(run_id) if p["current_q"] > 0]
    return sorted(ps, key=lambda p: (-p["score"], -p["current_q"], p["seconds"] or 0))


def stop_run(run_id: int) -> list[sqlite3.Row]:
    with closing(_connect()) as con, con:
        cur = con.execute("UPDATE quiz_runs SET status='stopped', stopped_at=? WHERE id=? AND status='active'",
                          (now_ms(), run_id))
        if cur.rowcount != 1:
            raise RuntimeError("Тест уже остановлен")
    ranked = ranking(run_id)
    with closing(_connect()) as con, con:
        for place, p in enumerate(ranked, 1):
            con.execute("UPDATE quiz_participants SET place=? WHERE run_id=? AND tg_id=?", (place, run_id, p["tg_id"]))
    return ranked


def run_answers(run_id: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM quiz_answers WHERE run_id=?", (run_id,))


def list_runs() -> list[sqlite3.Row]:
    return _all("""SELECT r.*,
                     (SELECT COUNT(*) FROM quiz_participants p WHERE p.run_id=r.id) AS participants,
                     (SELECT COUNT(*) FROM quiz_participants p WHERE p.run_id=r.id AND p.current_q>0) AS answered,
                     (SELECT COUNT(*) FROM quiz_participants p WHERE p.run_id=r.id AND p.finished_at IS NOT NULL) AS finished,
                     (SELECT AVG(score) FROM quiz_participants p WHERE p.run_id=r.id AND p.current_q>0) AS avg_score
                   FROM quiz_runs r ORDER BY r.id DESC""")


# ---------- Переписка ----------

def add_message(tg_id: int, direction: str, text: str | None,
                media_type: str | None = None, file_id: str | None = None) -> int:
    with closing(_connect()) as con, con:
        cur = con.execute(
            "INSERT INTO messages (tg_id, direction, text, media_type, file_id, ts, is_read) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tg_id, direction, text, media_type, file_id, now(), int(direction == "out")))
        return cur.lastrowid


def conversations() -> list[sqlite3.Row]:
    return _all("""SELECT u.*, m.text AS last_text, m.media_type AS last_media, m.direction AS last_dir,
                          m.ts AS last_ts,
                          (SELECT COUNT(*) FROM messages x WHERE x.tg_id=u.tg_id AND x.direction='in' AND x.is_read=0) AS unread
                   FROM users u
                   JOIN messages m ON m.id = (SELECT MAX(id) FROM messages WHERE tg_id=u.tg_id)
                   ORDER BY m.id DESC""")


def thread(tg_id: int, after_id: int = 0) -> list[sqlite3.Row]:
    return _all("SELECT * FROM messages WHERE tg_id=? AND id>? ORDER BY id", (tg_id, after_id))


def mark_read(tg_id: int) -> None:
    _exec("UPDATE messages SET is_read=1 WHERE tg_id=? AND direction='in' AND is_read=0", (tg_id,))


def unread_total() -> int:
    return _all("SELECT COUNT(*) FROM messages WHERE direction='in' AND is_read=0")[0][0]


def get_message(msg_id: int):
    rows = _all("SELECT * FROM messages WHERE id=?", (msg_id,))
    return rows[0] if rows else None
