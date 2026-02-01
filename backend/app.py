import os
import json
import random
from pydantic import BaseModel
from fastapi import FastAPI
from telethon import TelegramClient, events
import redis

from db import init_db, is_processed
from auth import login as do_login
from auth import auth_required

from api.system import router as system_router
from api.deadletters import router as deadletters_router

# === Telegram 配置 ===
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
SESSION = os.getenv("TG_SESSION", "userbot")

# Redis
r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)

# TG Client
client = TelegramClient(SESSION, TG_API_ID, TG_API_HASH)

# FastAPI
app = FastAPI()

# 公开部署：除 /api/login 外，其余管理 API 统一加 JWT 鉴权
admin_api = FastAPI(dependencies=[__import__("fastapi").Depends(auth_required)])
admin_api.include_router(system_router)
admin_api.include_router(deadletters_router)
app.mount("", admin_api)


class LoginReq(BaseModel):
    password: str


def _safe_json_load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return default
            return json.loads(raw)
    except FileNotFoundError:
        return default
    except Exception:
        # 配置损坏时不要让服务直接挂掉，回退到默认
        return default


def load_mapping():
    # 期望结构：{"-100xxx": {enabled, qq_channel_id, gray_ratio, filter, template, ...}}
    return _safe_json_load("mapping.json", {})


def load_blacklist():
    # 期望结构：["-100xxx", "-100yyy"]
    return _safe_json_load("blacklist.json", [])


def _normalize_gray_ratio(v) -> float:
    """兼容两种写法：
    - 0~1 概率（推荐）
    - 0~100 百分比（旧前端 UI 可能会写入）
    """
    try:
        x = float(v)
    except Exception:
        return 1.0

    if x <= 0:
        return 0.0
    if x > 1:
        # 认为是百分比
        return min(x / 100.0, 1.0)
    return x


def pass_filter(text: str, rule: dict) -> bool:
    """
    关键词/正则过滤（只看文字）
    mode:
      - block：命中即丢弃
      - allow：命中才转发
    """
    if not rule:
        return True
    import re

    text = text or ""
    mode = rule.get("mode", "block")
    keywords = rule.get("keywords", [])
    regexs = rule.get("regex", [])

    hit = any(k in text for k in keywords) if keywords else False
    if not hit and regexs:
        for rg in regexs:
            if re.search(rg, text):
                hit = True
                break

    return (not hit) if mode == "block" else hit


@client.on(events.NewMessage)
async def on_new_message(event):
    """
    监听 TG 新消息：
    - 只监听 mapping.json 配置的频道（白名单）
    - 黑名单直接丢弃
    - 关键词过滤只看文字
    - 多图策略：只转第一张（Telethon NewMessage 对相册会多次触发，这里做“单图/首图”简单策略）
    """
    chat_id_str = str(event.chat_id)
    msg_id = event.message.id

    # 去重：已成功转发过就不再处理
    if is_processed(event.chat_id, msg_id):
        return

    mapping = load_mapping()
    blacklist = load_blacklist()

    # 黑名单优先级最高
    if chat_id_str in blacklist:
        return

    conf = mapping.get(chat_id_str)
    if not conf:
        # 白名单监听：没配置就不处理
        return

    if not conf.get("enabled", True):
        return

    if random.random() > _normalize_gray_ratio(conf.get("gray_ratio", 1)):
        return

    text = event.message.text or ""

    if not pass_filter(text, conf.get("filter")):
        return

    # 只取第一张图（单条消息层面）
    media = None
    if event.message.photo:
        media = f"/tmp/{chat_id_str}_{msg_id}.jpg"
        await event.message.download_media(media)

    payload = {
        "chat_id": int(event.chat_id),
        "msg_id": int(msg_id),
        "text": text,
        "media": media,
        "qq_channel_id": conf["qq_channel_id"],
        "template": conf.get("template"),
        "channel_name": getattr(event.chat, "title", "") or "",
    }

    r.lpush("queue", json.dumps(payload, ensure_ascii=False))


@app.post("/api/login")
def api_login(req: LoginReq):
    """
    前端 axios.post('/api/login', {password})
    必须用 Pydantic Body，否则 FastAPI 会当 query 参数
    """
    return {"token": do_login(req.password)}


if __name__ == "__main__":
    init_db()
    client.start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
