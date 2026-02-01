import os
import json
import psycopg2
from psycopg2.extras import Json

# 单连接足够个人使用；如未来要高并发再上连接池
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
conn.autocommit = True


def init_db():
    """
    初始化表结构（幂等）
    - processed：成功转发记录（用于去重 + 今日成功统计）
    - dead：失败记录（用于死信查看 + 重放）
    """
    with conn.cursor() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS processed (
            tg_chat_id BIGINT NOT NULL,
            tg_msg_id  BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tg_chat_id, tg_msg_id)
        );
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS dead (
            id SERIAL PRIMARY KEY,
            tg_chat_id BIGINT NOT NULL,
            tg_msg_id  BIGINT NOT NULL,
            error TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        # 常用查询索引（死信列表按时间倒序）
        c.execute("CREATE INDEX IF NOT EXISTS idx_dead_created_at ON dead(created_at DESC);")


def is_processed(chat_id: int, msg_id: int) -> bool:
    with conn.cursor() as c:
        c.execute(
            "SELECT 1 FROM processed WHERE tg_chat_id=%s AND tg_msg_id=%s",
            (chat_id, msg_id)
        )
        return c.fetchone() is not None


def mark_processed(chat_id: int, msg_id: int):
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO processed (tg_chat_id, tg_msg_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (chat_id, msg_id)
        )


def save_dead(chat_id: int, msg_id: int, error: str, payload: dict):
    """
    payload 存原始任务，便于重放
    """
    with conn.cursor() as c:
        c.execute(
            "INSERT INTO dead (tg_chat_id, tg_msg_id, error, payload) VALUES (%s,%s,%s,%s)",
            (chat_id, msg_id, error, Json(payload))
        )


def list_dead(limit: int = 200):
    with conn.cursor() as c:
        c.execute("""
            SELECT id, tg_chat_id, tg_msg_id, error, payload, created_at
            FROM dead
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        rows = c.fetchall()

    # 转成 dict list，给 FastAPI 返回更舒服
    result = []
    for (id_, chat_id, msg_id, error, payload, created_at) in rows:
        result.append({
            "id": id_,
            "tg_chat_id": chat_id,
            "tg_msg_id": msg_id,
            "error": error,
            "payload": payload,
            "created_at": created_at.isoformat()
        })
    return result


def get_dead_payloads_by_ids(ids: list[int]):
    if not ids:
        return []

    with conn.cursor() as c:
        c.execute("""
            SELECT id, payload FROM dead
            WHERE id = ANY(%s)
        """, (ids,))
        rows = c.fetchall()

    return [{"id": r[0], "payload": r[1]} for r in rows]


def delete_dead_by_ids(ids: list[int]):
    if not ids:
        return
    with conn.cursor() as c:
        c.execute("DELETE FROM dead WHERE id = ANY(%s)", (ids,))


def stats_today():
    """
    提供 Dashboard 需要的指标：
    - success_today：今日成功（processed）
    - failed_today：今日失败（dead）
    - dead_count：当前死信总数
    """
    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) FROM processed WHERE created_at::date = CURRENT_DATE;")
        success_today = int(c.fetchone()[0])

        c.execute("SELECT COUNT(*) FROM dead WHERE created_at::date = CURRENT_DATE;")
        failed_today = int(c.fetchone()[0])

        c.execute("SELECT COUNT(*) FROM dead;")
        dead_count = int(c.fetchone()[0])

    return success_today, failed_today, dead_count
