import os
import json
import sqlite3
import threading

DB_PATH = os.getenv("DB_PATH", "/app/data/tg2qq.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed (
            tg_chat_id INTEGER NOT NULL,
            tg_msg_id  INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (tg_chat_id, tg_msg_id)
        );
        CREATE TABLE IF NOT EXISTS dead (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_chat_id INTEGER NOT NULL,
            tg_msg_id  INTEGER NOT NULL,
            error      TEXT NOT NULL,
            payload    TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dead_created_at ON dead(created_at DESC);
    """)
    conn.commit()


def is_processed(chat_id: int, msg_id: int) -> bool:
    row = _get_conn().execute(
        "SELECT 1 FROM processed WHERE tg_chat_id=? AND tg_msg_id=?",
        (chat_id, msg_id)
    ).fetchone()
    return row is not None


def mark_processed(chat_id: int, msg_id: int):
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO processed (tg_chat_id, tg_msg_id) VALUES (?,?)",
        (chat_id, msg_id)
    )
    conn.commit()


def save_dead(chat_id: int, msg_id: int, error: str, payload: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO dead (tg_chat_id, tg_msg_id, error, payload) VALUES (?,?,?,?)",
        (chat_id, msg_id, error, json.dumps(payload, ensure_ascii=False))
    )
    conn.commit()


def list_dead(limit: int = 200):
    rows = _get_conn().execute(
        "SELECT id, tg_chat_id, tg_msg_id, error, payload, created_at FROM dead ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [
        {
            "id": r["id"],
            "tg_chat_id": r["tg_chat_id"],
            "tg_msg_id": r["tg_msg_id"],
            "error": r["error"],
            "payload": json.loads(r["payload"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_dead_payloads_by_ids(ids: list[int]):
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = _get_conn().execute(
        f"SELECT id, payload FROM dead WHERE id IN ({placeholders})", ids
    ).fetchall()
    return [{"id": r["id"], "payload": json.loads(r["payload"])} for r in rows]


def delete_dead_by_ids(ids: list[int]):
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    conn = _get_conn()
    conn.execute(f"DELETE FROM dead WHERE id IN ({placeholders})", ids)
    conn.commit()


def stats_today():
    conn = _get_conn()
    today = conn.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM processed WHERE date(created_at)=date('now')), "
        "(SELECT COUNT(*) FROM dead WHERE date(created_at)=date('now')), "
        "(SELECT COUNT(*) FROM dead)"
    ).fetchone()
    return int(today[0]), int(today[1]), int(today[2])
