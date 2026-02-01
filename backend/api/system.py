from fastapi import APIRouter
import os
import redis

from db import stats_today

router = APIRouter(prefix="/api/system", tags=["system"])

r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)

@router.get("/stats")
def get_system_stats():
    """
    Dashboard 核心运维指标
    - queue_length：Redis 队列长度（是否堆积）
    - success_today：今日成功转发数
    - failed_today：今日失败数（死信）
    - dead_count：当前死信总数
    """
    queue_length = int(r.llen("queue"))
    success_today, failed_today, dead_count = stats_today()

    return {
        "queue_length": queue_length,
        "success_today": success_today,
        "failed_today": failed_today,
        "dead_count": dead_count,
    }
