import os
import json
import random
import time
from pydantic import BaseModel
from fastapi import FastAPI
from telethon import TelegramClient, events
import redis

from db import init_db, is_processed
from auth import login as do_login
from auth import auth_required

from api.system import router as system_router
from api.deadletters import router as deadletters_router
from api.qq_debug import router as qq_debug_router

# === Telegram 配置 ===
TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
SESSION = os.getenv("TG_SESSION", "userbot")
TG_SESSION_DIR = (os.getenv("TG_SESSION_DIR") or "/app/sessions").rstrip("/")

# 确保 session 目录存在
try:
    os.makedirs(TG_SESSION_DIR, exist_ok=True)
except Exception:
    pass

# TG Client
client = TelegramClient(f"{TG_SESSION_DIR}/{SESSION}", TG_API_ID, TG_API_HASH)

# Redis
r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)

# FastAPI
app = FastAPI()

# 公开部署：/api/login 与 /healthz 不鉴权；其余管理 API 统一加 JWT 鉴权
# 不要把子应用 mount 到根路径（会遮住 app 上的公开路由）。
from fastapi import APIRouter, Depends

admin_router = APIRouter(dependencies=[Depends(auth_required)])
admin_router.include_router(system_router)
admin_router.include_router(deadletters_router)
admin_router.include_router(qq_debug_router)
app.include_router(admin_router)


class LoginReq(BaseModel):
    password: str


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
    """关键词/正则过滤（只看文字）

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


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_csv(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else str(v)


def _build_env_rule() -> dict:
    return {
        "enabled": _env_bool("FORWARD_ENABLED", True),
        "qq_channel_id": (os.getenv("QQ_TARGET_CHANNEL_ID") or "").strip(),
        "gray_ratio": os.getenv("GRAY_RATIO", "1"),
        "template": {
            "prefix": os.getenv("TEMPLATE_PREFIX", ""),
            "suffix": os.getenv("TEMPLATE_SUFFIX", ""),
        },
        "filter": {
            "mode": (os.getenv("FILTER_MODE") or "block").strip() or "block",
            "keywords": _env_csv("FILTER_KEYWORDS"),
            "regex": _env_csv("FILTER_REGEX"),
        },
    }


# === 纯 ENV 模式：username -> chat_id 白名单缓存 ===
_ENV_RESOLVED_SOURCES: set[str] = set()


def _log(level: str, msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][{level}] {msg}")


async def refresh_env_sources_cache() -> set[str]:
    """解析 TG_SOURCES（多个 @username）为 peer_id 字符串集合。"""
    from telethon.utils import get_peer_id

    sources = _env_csv("TG_SOURCES")
    resolved: set[str] = set()

    for u in sources:
        if not u.startswith("@"): 
            u = "@" + u
        try:
            entity = await client.get_entity(u)
            resolved.add(str(get_peer_id(entity)))
        except Exception as e:
            _log("WARN", f"resolve tg source failed: {u} err={e}")
            continue

    global _ENV_RESOLVED_SOURCES
    _ENV_RESOLVED_SOURCES = resolved
    _log("INFO", f"tg sources resolved: {len(_ENV_RESOLVED_SOURCES)} / {len(sources)}")
    return resolved


def _debug_tg_events_enabled() -> bool:
    return _env_bool("DEBUG_TG_EVENTS", False)


@client.on(events.NewMessage)
async def on_new_message(event):
    """纯 ENV 模式：
    - 仅监听 TG_SOURCES 解析出的 chat_id
    - 统一发送到 QQ_TARGET_CHANNEL_ID（若留空由 worker 通过 guild 自动选）

    调试：
    - DEBUG_TG_EVENTS=true 时会打印每条消息的判定路径，帮助定位“TG 有消息但没入队”。
    """
    chat_id_str = str(event.chat_id)
    msg_id = event.message.id

    if _debug_tg_events_enabled():
        _log(
            "INFO",
            f"tg_event recv: chat_id={chat_id_str} msg_id={msg_id} "
            f"resolved_sources={len(_ENV_RESOLVED_SOURCES)}",
        )

    # 去重
    if is_processed(event.chat_id, msg_id):
        if _debug_tg_events_enabled():
            _log("INFO", f"tg_event drop: processed chat_id={chat_id_str} msg_id={msg_id}")
        return

    # 白名单
    if not _ENV_RESOLVED_SOURCES:
        if _debug_tg_events_enabled():
            _log("WARN", "tg_event drop: resolved sources empty (did refresh_env_sources_cache run?)")
        return

    if chat_id_str not in _ENV_RESOLVED_SOURCES:
        if _debug_tg_events_enabled():
            # 只打印前几个，避免太长
            sample = list(sorted(_ENV_RESOLVED_SOURCES))[:10]
            _log(
                "INFO",
                f"tg_event drop: chat_id not in whitelist chat_id={chat_id_str} sample_whitelist={sample}",
            )
        return

    conf = _build_env_rule()

    if not conf.get("enabled", True):
        if _debug_tg_events_enabled():
            _log("INFO", "tg_event drop: FORWARD_ENABLED=false")
        return

    if random.random() > _normalize_gray_ratio(conf.get("gray_ratio", 1)):
        if _debug_tg_events_enabled():
            _log("INFO", f"tg_event drop: gray_ratio gate gray_ratio={conf.get('gray_ratio')}")
        return

    text = event.message.text or ""

    if not pass_filter(text, conf.get("filter")):
        if _debug_tg_events_enabled():
            _log("INFO", "tg_event drop: filter blocked")
        return

    media = None
    if event.message.photo:
        media = f"/tmp/{chat_id_str}_{msg_id}.jpg"
        await event.message.download_media(media)

    payload = {
        "chat_id": int(event.chat_id),
        "msg_id": int(msg_id),
        "text": text,
        "media": media,
        # 纯 env 模式：qq_channel_id 可以为空，worker 会用 QQ_TARGET_GUILD_ID 自动选择
        "qq_channel_id": conf.get("qq_channel_id") or "",
        "template": conf.get("template"),
        "channel_name": getattr(event.chat, "title", "") or "",
    }

    r.lpush("queue", json.dumps(payload, ensure_ascii=False))

    if _debug_tg_events_enabled():
        _log(
            "INFO",
            f"tg_event enqueued ok: chat_id={chat_id_str} msg_id={msg_id} has_media={bool(media)}",
        )


@app.post("/api/login")
def api_login(req: LoginReq):
    return {"token": do_login(req.password)}


@app.get("/healthz")
def healthz():
    """不鉴权健康检查。

    用于：Nginx/容器平台探活、以及你部署后快速确认服务是否启动。
    注意：不返回 secret/token。
    """
    sources = _env_csv("TG_SOURCES")
    return {
        "ok": True,
        "service": "tg2qqpd-backend",
        "has_tg_api": bool(os.getenv("TG_API_ID")) and bool(os.getenv("TG_API_HASH")),
        "tg_session": SESSION,
        "tg_sources_count": len(sources),
        "tg_sources_resolved": len(_ENV_RESOLVED_SOURCES),
        "forward_enabled": _env_bool("FORWARD_ENABLED", True),
        "gray_ratio": os.getenv("GRAY_RATIO", "1"),
        "has_qq_target_channel": bool((os.getenv("QQ_TARGET_CHANNEL_ID") or "").strip()),
        "has_qq_target_guild": bool((os.getenv("QQ_TARGET_GUILD_ID") or "").strip()),
    }


if __name__ == "__main__":
    init_db()

    _log(
        "INFO",
        "backend boot: "
        f"tg_session={SESSION} "
        f"tg_sources={os.getenv('TG_SOURCES','').strip()} "
        f"forward_enabled={os.getenv('FORWARD_ENABLED','true')} "
        f"gray_ratio={os.getenv('GRAY_RATIO','1')} "
        f"has_qq_target_channel={bool((os.getenv('QQ_TARGET_CHANNEL_ID') or '').strip())}",
    )

    # 首次部署：容器后台运行无法交互输入验证码/手机号。
    # 方案：运维先用 TG_LOGIN_ONLY=1 交互式跑一次生成 session 文件，再正常启动。
    if _env_bool("TG_LOGIN_ONLY", False):
        _log("INFO", "TG_LOGIN_ONLY=1 set, will run Telethon interactive login only.")
        client.start()  # 这里会要求输入手机号/验证码
        _log("INFO", "Telethon login done. Session saved. Now exit.")
        raise SystemExit(0)

    # 正常启动：若 session 不存在且需要交互输入，会在容器中 EOF。
    # 这里捕获后给出明确指引，避免无限重启刷屏。
    try:
        client.start()
    except EOFError:
        _log(
            "ERROR",
            "Telethon requires interactive login (phone/code), but container is non-interactive. "
            "Please run one-time login: set TG_LOGIN_ONLY=1 and start backend in foreground with tty, "
            "then input phone/code to generate session under TG_SESSION_DIR.",
        )
        raise

    # 启动时解析一次 TG_SOURCES
    try:
        with client:
            client.loop.run_until_complete(refresh_env_sources_cache())
    except Exception as e:
        _log("ERROR", f"refresh_env_sources_cache failed: {e}")

    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
