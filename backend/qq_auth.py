import os
import time
import threading
import requests

from config import get as cfg_get

BOT_API_BASE = str(cfg_get("qq.api_base", "https://api.sgroup.qq.com")).rstrip("/")

APP_ID = str(cfg_get("qq.app_id", "")).strip()
APP_SECRET = str(cfg_get("qq.app_secret", "")).strip()

# 兼容：若用户手动提供 access_token，则优先用它（不做刷新）
MANUAL_ACCESS_TOKEN = str(cfg_get("qq.access_token", "")).strip()

# 提前多少秒刷新 token
REFRESH_SKEW_SECONDS = int(cfg_get("qq.access_token_refresh_skew", 60))

_lock = threading.Lock()
_cached_token: str | None = None
_expires_at: float = 0.0
_last_refresh_error: str | None = None
_last_refresh_at: float = 0.0


def _fetch_access_token() -> tuple[str, int]:
    """从 bots.qq.com 获取 access_token。

    文档：POST https://bots.qq.com/app/getAppAccessToken
    body: {appId, clientSecret}
    resp: {access_token, expires_in}
    """
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("QQ_APP_ID/QQ_APP_SECRET is required to auto refresh access_token")

    resp = requests.post(
        "https://bots.qq.com/app/getAppAccessToken",
        headers={"Content-Type": "application/json"},
        json={"appId": APP_ID, "clientSecret": APP_SECRET},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    token = (data.get("access_token") or "").strip()
    expires_in = int(data.get("expires_in") or 0)
    if not token or expires_in <= 0:
        raise RuntimeError(f"invalid access_token response: {data}")
    return token, expires_in


def get_access_token(force_refresh: bool = False) -> str:
    """获取可用 access_token（带轻量缓存与自动刷新）。

    force_refresh=True 可用于在鉴权失败后强制重新拉取。
    """
    global _cached_token, _expires_at, _last_refresh_error, _last_refresh_at

    if MANUAL_ACCESS_TOKEN:
        return MANUAL_ACCESS_TOKEN

    now = time.time()
    with _lock:
        if (
            (not force_refresh)
            and _cached_token
            and now < (_expires_at - REFRESH_SKEW_SECONDS)
        ):
            return _cached_token

        try:
            token, expires_in = _fetch_access_token()
            _cached_token = token
            _expires_at = now + expires_in
            _last_refresh_error = None
            _last_refresh_at = now
            return _cached_token
        except Exception as e:
            _last_refresh_error = str(e)
            # 若曾经有可用 token，允许继续用旧 token（给重试/恢复留机会）
            if _cached_token:
                return _cached_token
            raise


def get_token_status() -> dict:
    """用于日志/排查，不包含 secret。"""
    now = time.time()
    with _lock:
        return {
            "has_manual_token": bool(MANUAL_ACCESS_TOKEN),
            "has_cached_token": bool(_cached_token),
            "expires_in": max(int(_expires_at - now), 0),
            "last_refresh_at": int(_last_refresh_at) if _last_refresh_at else 0,
            "last_refresh_error": _last_refresh_error,
        }


def auth_headers(force_refresh: bool = False) -> dict:
    """OpenAPI 统一鉴权 Header：Authorization: QQBot {ACCESS_TOKEN}"""
    return {"Authorization": f"QQBot {get_access_token(force_refresh=force_refresh)}"}
