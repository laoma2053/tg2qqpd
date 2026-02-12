from fastapi import APIRouter, Body
import json
import os
import redis

from db import list_dead, get_dead_payloads_by_ids, delete_dead_by_ids

router = APIRouter(prefix="/api/deadletters", tags=["deadletters"])
r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)


@router.get("")
def api_list_deadletters():
    """
    死信列表
    """
    rows = list_dead(limit=200)

    # 给前端更友好：补一个 content 预览字段
    # payload 结构与 worker 入队一致：{text, media, ...}
    for item in rows:
        payload = item.get("payload") or {}
        item["content"] = (payload.get("text") or "")[:200]
        item["qq_channel_id"] = payload.get("qq_channel_id")
        item["channel_name"] = payload.get("channel_name")
    return rows


@router.post("/{dead_id}/retry")
def api_retry_one(dead_id: int):
    """
    单条重放：把 payload 再次推入 Redis 队列，并从 dead 删除
    """
    payloads = get_dead_payloads_by_ids([dead_id])
    if not payloads:
        return {"ok": False, "reason": "not_found"}

    payload = payloads[0]["payload"]
    r.lpush("queue", json.dumps(payload, ensure_ascii=False))
    delete_dead_by_ids([dead_id])
    return {"ok": True}


@router.post("/retry")
def api_retry_batch(ids: list[int] = Body(..., embed=True)):
    """
    批量重放：ids = [1,2,3]
    """
    payload_rows = get_dead_payloads_by_ids(ids)
    for row in payload_rows:
        payload = row["payload"]
        r.lpush("queue", json.dumps(payload, ensure_ascii=False))

    # 入队后删除死信（避免重复重放）
    delete_dead_by_ids([r["id"] for r in payload_rows])
    return {"ok": True, "count": len(payload_rows)}
