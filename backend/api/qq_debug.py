from __future__ import annotations

import os
import requests
from fastapi import APIRouter, HTTPException, Query

from qq_auth import auth_headers

router = APIRouter(prefix="/api/qq", tags=["qq"])

BOT_API_BASE = os.getenv("QQ_API_BASE", "https://api.sgroup.qq.com").rstrip("/")


@router.get("/guilds")
def list_guilds():
    """列出机器人加入的所有频道（guilds）。

    用途：你只有邀请链接/pd 号时，通过这个接口找到真正的 guild_id（数字）。
    """
    resp = requests.get(f"{BOT_API_BASE}/users/@me/guilds", headers=auth_headers(), timeout=20)
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.get("/channels")
def list_channels(guild_id: str = Query(..., description="QQ 频道 guild_id（数字）")):
    """列出指定 guild 下的所有子频道（channels）。"""
    resp = requests.get(
        f"{BOT_API_BASE}/guilds/{guild_id}/channels",
        headers=auth_headers(),
        timeout=20,
    )
    if not resp.ok:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@router.get("/pick-default-channel")
def pick_default_channel(guild_id: str = Query(..., description="QQ 频道 guild_id（数字）")):
    """从 guild 下自动选择一个可用的“可发言频道”，返回其 channel_id。

    兼容：
    - type=0（旧的文字子频道）
    - type=10007/10011 等（你当前频道返回就是这些）

    策略：优先选择 speak_permission==1 且类型优先（0/10007/10011），跳过分类(type=4)。

    Pick a default channel from the guild with priority on speakable channels and specific types.
    """
    data = list_channels(guild_id)

    channels = data
    if isinstance(data, dict):
        channels = data.get("data") or data.get("channels") or data.get("items") or []

    if not isinstance(channels, list):
        raise HTTPException(status_code=500, detail="unexpected channels response")

    preferred_types = [0, 10007, 10011]
    skip_types = {4}

    def _cid(ch: dict) -> str | None:
        cid = ch.get("id") or ch.get("channel_id")
        return str(cid) if cid else None

    def _type_int(ch: dict) -> int | None:
        try:
            return int(ch.get("type"))
        except Exception:
            return None

    def _can_speak(ch: dict) -> bool:
        try:
            return int(ch.get("speak_permission", 0)) == 1
        except Exception:
            return False

    # 1) 优先：可发言 + 优先类型
    for t in preferred_types:
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            if _type_int(ch) in skip_types:
                continue
            if _type_int(ch) == t and _can_speak(ch):
                cid = _cid(ch)
                if cid:
                    return {"channel_id": cid, "channel": ch}

    # 2) 次优：优先类型
    for t in preferred_types:
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            if _type_int(ch) in skip_types:
                continue
            if _type_int(ch) == t:
                cid = _cid(ch)
                if cid:
                    return {"channel_id": cid, "channel": ch}

    # 3) 兜底：任何可发言频道
    for ch in channels:
        if not isinstance(ch, dict):
            continue
        if _type_int(ch) in skip_types:
            continue
        if _can_speak(ch):
            cid = _cid(ch)
            if cid:
                return {"channel_id": cid, "channel": ch}

    raise HTTPException(status_code=404, detail="no speakable channel found")
