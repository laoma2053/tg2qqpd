import os
import json
import random
import re
import time
from pydantic import BaseModel
from fastapi import FastAPI
from telethon import TelegramClient, events
import redis

from config import CFG, get as cfg_get
from db import init_db, is_processed
from auth import login as do_login
from auth import auth_required

from api.system import router as system_router
from api.deadletters import router as deadletters_router
from api.qq_debug import router as qq_debug_router

# === Telegram 配置（从 config.yaml）===
TG_API_ID = int(cfg_get("telegram.api_id"))
TG_API_HASH = cfg_get("telegram.api_hash")
SESSION = cfg_get("telegram.session", "userbot")
TG_SESSION_DIR = (cfg_get("telegram.session_dir") or "/app/sessions").rstrip("/")

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
    """关键词/正则过滤（黑名单优先 + 可选白名单）

    逻辑（与另一个 TG 项目的 filter_text 完全对齐）：
    1) block 命中 → 直接丢弃（最高优先级）
    2) 若 require_allows=true，则必须命中 allow 关键词/正则才放行
    3) 若 require_allows=false（默认），allow 不生效，block 没命中就放行

    配置来源：config.yaml → rules.filter
    """
    if not rule:
        return True

    text = text or ""

    # --- block（黑名单）：命中即丢弃 ---
    block_kw = rule.get("block_keywords") or rule.get("keywords") or []
    block_re = rule.get("block_regex") or rule.get("regex") or []

    if block_kw and any(k in text for k in block_kw):
        return False
    if block_re:
        for rg in block_re:
            try:
                if re.search(rg, text):
                    return False
            except re.error:
                pass

    # --- allow（白名单）：require_allows=true 时必须命中才放行 ---
    require_allows = rule.get("require_allows", False)
    if require_allows:
        allow_kw = rule.get("allow_keywords") or []
        allow_re = rule.get("allow_regex") or []

        if not allow_kw and not allow_re:
            # 配置了 require_allows 但没给任何 allow 规则 → 全部放行（避免误杀）
            return True

        hit_allow = False
        if allow_kw and any(k in text for k in allow_kw):
            hit_allow = True
        if not hit_allow and allow_re:
            for rg in allow_re:
                try:
                    if re.search(rg, text):
                        hit_allow = True
                        break
                except re.error:
                    pass
        return hit_allow

    return True


def _build_forward_conf() -> dict:
    """从 config.yaml 构建转发配置（每次消息调用，支持热重载后的潜在扩展）。"""
    fwd = CFG.get("forward") or {}
    fltr = cfg_get("rules.filter") or {}
    return {
        "enabled": fwd.get("enabled", True),
        "qq_channel_id": str(cfg_get("qq.target_channel_id") or "").strip(),
        "gray_ratio": fwd.get("gray_ratio", 1),
        "template": {
            "prefix": fwd.get("template_prefix", ""),
            "suffix": fwd.get("template_suffix", ""),
        },
        "filter": fltr,
    }


# === TG 源 -> chat_id 白名单缓存 ===
_ENV_RESOLVED_SOURCES: set[str] = set()


def _log(level: str, msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][{level}] {msg}")


async def refresh_env_sources_cache() -> list[int]:
    """解析 telegram.sources 为 peer_id 集合，同时返回 int id 列表供 chats= 过滤。"""
    from telethon.utils import get_peer_id

    sources = cfg_get("telegram.sources") or []
    resolved: set[str] = set()
    entity_ids: list[int] = []

    for u in sources:
        u = str(u).strip()
        if not u:
            continue
        if not u.startswith("@"):
            u = "@" + u
        try:
            entity = await client.get_entity(u)
            pid = str(get_peer_id(entity))
            resolved.add(pid)
            entity_ids.append(int(pid))
        except Exception as e:
            _log("WARN", f"resolve tg source failed: {u} err={e}")
            continue

    global _ENV_RESOLVED_SOURCES
    _ENV_RESOLVED_SOURCES = resolved
    _log("INFO", f"tg sources resolved: {len(_ENV_RESOLVED_SOURCES)} / {len(sources)}")
    return entity_ids


def _debug_tg_events_enabled() -> bool:
    return cfg_get("logging.debug_tg_events", False)


# 注意：不再用 @client.on(events.NewMessage) 静态注册。
# 改为在 _startup() 中解析完 TG_SOURCES 后，用 chats= 参数动态注册，
# 这样 Telethon 底层只派发白名单频道的消息，其他频道的消息根本不进入回调。

async def on_new_message(event):
    """纯 ENV 模式：
    - Telethon 底层已通过 chats= 过滤，只有白名单频道的消息才会触发本回调
    - 统一发送到 QQ_TARGET_CHANNEL_ID（若留空由 worker 通过 guild 自动选）
    """
    chat_id_str = str(event.chat_id)
    msg_id = event.message.id

    if _debug_tg_events_enabled():
        _log(
            "INFO",
            f"tg_event recv: chat_id={chat_id_str} msg_id={msg_id}",
        )

    # 去重
    if is_processed(event.chat_id, msg_id):
        if _debug_tg_events_enabled():
            _log("INFO", f"tg_event drop: processed chat_id={chat_id_str} msg_id={msg_id}")
        return

    conf = _build_forward_conf()

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
    """不鉴权健康检查。"""
    sources = cfg_get("telegram.sources") or []
    fwd = CFG.get("forward") or {}
    return {
        "ok": True,
        "service": "tg2qqpd-backend",
        "has_tg_api": bool(cfg_get("telegram.api_id")) and bool(cfg_get("telegram.api_hash")),
        "tg_session": SESSION,
        "tg_sources_count": len(sources),
        "tg_sources_resolved": len(_ENV_RESOLVED_SOURCES),
        "forward_enabled": fwd.get("enabled", True),
        "gray_ratio": fwd.get("gray_ratio", 1),
        "has_qq_target_channel": bool(str(cfg_get("qq.target_channel_id") or "").strip()),
        "has_qq_target_guild": bool(str(cfg_get("qq.target_guild_id") or "").strip()),
    }


if __name__ == "__main__":
    import asyncio
    import uvicorn

    init_db()

    fwd = CFG.get("forward") or {}
    _log(
        "INFO",
        "backend boot: "
        f"tg_session={SESSION} "
        f"tg_sources={cfg_get('telegram.sources', [])} "
        f"forward_enabled={fwd.get('enabled', True)} "
        f"gray_ratio={fwd.get('gray_ratio', 1)} "
        f"has_qq_target_channel={bool(str(cfg_get('qq.target_channel_id') or '').strip())}",
    )

    # 首次部署：容器后台运行无法交互输入验证码/手机号。
    # 方案：运维先用 TG_LOGIN_ONLY=1 交互式跑一次生成 session 文件，再正常启动。
    if os.getenv("TG_LOGIN_ONLY", "").strip().lower() in ("1", "true", "yes"):
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

    # ---------- 关键：让 Telethon 和 Uvicorn 共享同一个 asyncio 事件循环 ----------
    # Telethon 必须持续运行在 asyncio loop 里才能收到消息事件。
    # 之前的 uvicorn.run() 会创建新的事件循环，Telethon 的 event handler 不会被触发。
    # 解决方案：在 Telethon 已有的 loop 中用 uvicorn.Server 启动 HTTP 服务。

    loop = client.loop  # Telethon 已绑定的 asyncio 事件循环

    async def _startup():
        """在同一个事件循环里完成：TG 源解析 -> 注册事件监听 -> Uvicorn 启动。"""
        # 确保 Telethon 连接仍然有效
        if not client.is_connected():
            await client.connect()

        # 解析 TG_SOURCES，返回 entity id 列表
        entity_ids = []
        try:
            entity_ids = await refresh_env_sources_cache()
        except Exception as e:
            _log("ERROR", f"refresh_env_sources_cache failed: {e}")

        # 动态注册事件处理器，chats= 限定只接收白名单频道的消息
        # 这样 Telethon 底层直接过滤，其他频道的消息根本不会进入回调
        if entity_ids:
            client.add_event_handler(
                on_new_message,
                events.NewMessage(chats=entity_ids),
            )
            _log("INFO", f"Telethon event handler registered: chats={entity_ids}")
        else:
            _log("WARN", "No TG sources resolved, event handler NOT registered (no messages will be forwarded)")

        _log("INFO", "Telethon event loop is running - TG messages will now be received.")

        # 启动 Uvicorn（作为同一个 loop 内的 Server，不会抢占事件循环）
        config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="none")
        server = uvicorn.Server(config)
        await server.serve()

    # 运行：Telethon event handler（on_new_message）和 Uvicorn 同时活跃在同一个 loop
    with client:
        loop.run_until_complete(_startup())
