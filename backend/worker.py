import os
import json
import time
import requests
import redis
from PIL import Image
import re
import yaml

from config import CFG, get as cfg_get
from db import mark_processed, save_dead

from qq_auth import auth_headers, get_token_status
from qq_ws_keepalive import QQWsKeepAlive

r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)

BOT_API_BASE = str(cfg_get("qq.api_base", "https://api.sgroup.qq.com")).rstrip("/")

# 目标频道
QQ_TARGET_CHANNEL_ID = str(cfg_get("qq.target_channel_id") or "").strip()
QQ_TARGET_GUILD_ID = str(cfg_get("qq.target_guild_id") or "").strip()


def _log(level: str, msg: str):
    # 简单结构化，方便 docker logs grep
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][{level}] {msg}")


# 防风控：发送间隔（秒）
try:
    SEND_INTERVAL = float(cfg_get("qq.send_interval", 1.5))
except Exception:
    SEND_INTERVAL = 1.5

# 静默时段（QQ 频道 00:00~06:00 禁止主动消息）
QUIET_HOURS_START = int(cfg_get("qq.quiet_hours_start", 0))
QUIET_HOURS_END = int(cfg_get("qq.quiet_hours_end", 6))


def _in_quiet_hours() -> bool:
    """检查当前是否处于静默时段。"""
    hour = time.localtime().tm_hour
    if QUIET_HOURS_START < QUIET_HOURS_END:
        return QUIET_HOURS_START <= hour < QUIET_HOURS_END
    else:  # 跨午夜，例如 22~6
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def _guess_first_text_channel_id() -> str | None:
    """从 guild_id 自动挑选一个可用的“可发言频道” channel_id。

    兼容：
    - 旧逻辑：type=0（文字）
    - 新频道形态：type=10007/10011 等（你当前实际返回就是这些）

    选择策略：
    1) 优先选择 speak_permission == 1 的频道
    2) type 优先顺序：0 -> 10007/10011/…（可扩展）
    3) 跳过明显的分类节点（常见 type=4）
    4) 实在找不到就回退到第一个带 id 的频道
    """
    if not QQ_TARGET_GUILD_ID:
        return None

    # 已知可发消息的类型（会因 QQ 频道形态而变化，先覆盖常见）
    preferred_types = [0, 10007, 10011]
    skip_types = {4}

    try:
        resp = requests.get(
            f"{BOT_API_BASE}/guilds/{QQ_TARGET_GUILD_ID}/channels",
            headers=auth_headers(),
            timeout=15,
        )
        if not resp.ok:
            _log(
                "ERROR",
                f"guild channels list failed. guild_id={QQ_TARGET_GUILD_ID} status={resp.status_code} body={resp.text}",
            )
            return None

        data = resp.json()
        channels = data
        if isinstance(data, dict):
            channels = data.get("data") or data.get("channels") or data.get("items") or []

        if not isinstance(channels, list):
            return None

        def _cid(ch: dict) -> str | None:
            cid = ch.get("id") or ch.get("channel_id")
            return str(cid) if cid else None

        def _type_int(ch: dict) -> int | None:
            try:
                return int(ch.get("type"))
            except Exception:
                return None

        def _can_speak(ch: dict) -> bool:
            # speak_permission: 1 表示可发言（你返回里有这个字段）
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
                        return cid

        # 2) 次优：优先类型（不强制 speak_permission 字段存在）
        for t in preferred_types:
            for ch in channels:
                if not isinstance(ch, dict):
                    continue
                if _type_int(ch) in skip_types:
                    continue
                if _type_int(ch) == t:
                    cid = _cid(ch)
                    if cid:
                        return cid

        # 3) 兜底：任何可发言的频道
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            if _type_int(ch) in skip_types:
                continue
            if _can_speak(ch):
                cid = _cid(ch)
                if cid:
                    return cid

        # 4) 最后兜底：第一个有 id 的
        for ch in channels:
            if isinstance(ch, dict) and _cid(ch):
                return _cid(ch)

        return None
    except Exception as e:
        _log("ERROR", f"guild channels list exception. guild_id={QQ_TARGET_GUILD_ID} err={e}")
        return None


# worker 侧最终使用的目标 channel_id：
# - 优先使用 QQ_TARGET_CHANNEL_ID
# - 若为空且提供了 QQ_TARGET_GUILD_ID，则自动选择
DEFAULT_SEND_CHANNEL_ID = QQ_TARGET_CHANNEL_ID or _guess_first_text_channel_id() or ""

_log(
    "INFO",
    "worker boot: "
    f"api_base={BOT_API_BASE} "
    f"has_target_channel={bool(QQ_TARGET_CHANNEL_ID)} has_target_guild={bool(QQ_TARGET_GUILD_ID)} "
    f"default_send_channel_id={DEFAULT_SEND_CHANNEL_ID or '(empty)'} "
    f"send_interval={SEND_INTERVAL}",
)

# ============================================================
# YAML 驱动的文案清洗规则引擎
# ============================================================
# 规则来源：config.yaml → rules.transforms
# 修改 config.yaml 后 docker compose restart worker 即可生效。
# ============================================================

_FLAG_MAP = {
    "s": re.DOTALL,
    "m": re.MULTILINE,
    "i": re.IGNORECASE,
}

# 多空行收敛（内置，不可关闭）
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")


def _parse_flags(flags_str: str) -> int:
    """将 "msi" 这样的 flag 字符串转成 re 标志位组合。"""
    result = 0
    for ch in flags_str.lower():
        if ch in _FLAG_MAP:
            result |= _FLAG_MAP[ch]
    return result


def _load_transforms() -> list[dict]:
    """从 config.yaml 的 rules.transforms 加载并预编译清洗规则。

    返回列表，每个元素:
      - type="regex_replace": {"type", "compiled": re.Pattern, "repl": str}
      - type="append":        {"type", "text": str}
    """
    raw_rules = cfg_get("rules.transforms") or []
    compiled: list[dict] = []

    for idx, rule in enumerate(raw_rules):
        rtype = rule.get("type", "")
        if rtype == "regex_replace":
            pattern = rule.get("pattern", "")
            repl = rule.get("repl", "")
            flags_str = rule.get("flags", "ms")
            try:
                compiled.append({
                    "type": "regex_replace",
                    "compiled": re.compile(pattern, _parse_flags(flags_str)),
                    "repl": repl,
                })
            except re.error as e:
                _log("ERROR", f"transforms rule #{idx} regex compile failed: {e} pattern={pattern}")
        elif rtype == "append":
            text = rule.get("text", "")
            compiled.append({"type": "append", "text": text})
        else:
            _log("WARN", f"transforms rule #{idx} unknown type: {rtype}")

    _log("INFO", f"loaded {len(compiled)} transform rules from config.yaml")
    return compiled


_TRANSFORMS: list[dict] = _load_transforms()


def compress_image(src: str, max_size_mb: int = 9) -> str | None:
    """
    图片超限时压缩一次（质量递减）
    """
    try:
        img = Image.open(src).convert("RGB")
    except Exception:
        return None

    tmp = src.replace(".jpg", "_compressed.jpg")
    for q in (85, 75, 65, 55):
        img.save(tmp, "JPEG", quality=q)
        if os.path.getsize(tmp) <= max_size_mb * 1024 * 1024:
            return tmp
    return None


def apply_template(text: str, tpl: dict | None, ctx: dict) -> str:
    tpl = tpl or {}
    out = f"{tpl.get('prefix','')}{text or ''}{tpl.get('suffix','')}"
    for k, v in ctx.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
    return out


def _is_auth_error(resp: requests.Response | None, err_text: str | None = None) -> bool:
    if resp is None:
        return False
    if resp.status_code in (401, 403):
        return True
    # 兼容部分场景：返回体里会带 code/message
    try:
        data = resp.json() or {}
        msg = str(data.get("message") or "")
        code = str(data.get("code") or "")
        blob = (msg + " " + code + " " + (err_text or "")).lower()
        return any(k in blob for k in ("unauthorized", "token", "access_token", "auth"))
    except Exception:
        return False


def _is_online_required_error(resp: requests.Response | None, err_text: str | None = None) -> bool:
    if resp is None:
        return False
    try:
        data = resp.json() or {}
        msg = str(data.get("message") or "")
        code = str(data.get("code") or "")
        blob = (msg + " " + code + " " + (err_text or "")).lower()
        return any(k in blob for k in ("websocket", "ws", "offline", "not online"))
    except Exception:
        return False


def _is_rate_limited(resp: requests.Response | None) -> bool:
    """检测是否触发 QQ 频道消息发送频率/数量限制。"""
    if resp is None:
        return False
    try:
        data = resp.json() or {}
        code = int(data.get("code", 0))
        msg = str(data.get("message") or "").lower()
        # 304045 = push channel message reach limit
        # 304003 有时也用于频率限制
        return code == 304045 or "reach limit" in msg or "rate limit" in msg
    except Exception:
        return False


def _build_title_and_body(text: str) -> tuple[str, str]:
    """从清洗后的文本中提取帖子标题和正文。

    - title: 取第一行（去除 Markdown **加粗** 标记），截断 60 字
    - body:  去掉第一行后的剩余文本（避免标题与正文重复）
    """
    lines = (text or "").split("\n", 1)
    raw_title = lines[0].strip() if lines else "更新"
    # 去除 Markdown 加粗标记 **...**
    title = raw_title.replace("**", "").strip()[:60] or "更新"
    body = lines[1].strip() if len(lines) > 1 else ""
    return title, body


def send_text(channel_id: str, text: str):
    """发送纯文本到帖子频道（PUT /channels/{channel_id}/threads）。

    QQ 频道目前已没有 type=0 的纯文字子频道，
    所有子频道均为 type=10007（帖子频道），
    需使用 PUT threads API 代替 POST messages。

    帖子格式：format=1 为纯文本。
    title 取文本第一行，content 为剩余文本（避免标题重复显示）。
    """
    title, body = _build_title_and_body(text)
    return requests.put(
        f"{BOT_API_BASE}/channels/{channel_id}/threads",
        headers={
            **auth_headers(),
            "Content-Type": "application/json",
        },
        json={
            "title": title,
            "content": body,
            "format": 1,  # FORMAT_TEXT = 纯文本
        },
        timeout=15,
    )


def _build_richtext_json(body: str, image_url: str | None = None) -> str:
    """构建 RichText JSON 字符串，用于 format=4 发帖。

    RichText 结构：
      { "paragraphs": [ { "elems": [...], "props": {} }, ... ] }

    元素类型：
      ElemType 1 = TEXT,  2 = IMAGE,  4 = URL
    """
    paragraphs = []

    # ── 图片段落（放在正文前面，更醒目）──
    if image_url:
        paragraphs.append({
            "elems": [{
                "type": 2,  # ELEM_TYPE_IMAGE
                "image": {
                    "third_url": image_url,
                    "width_percent": 1.0,  # 100% 宽度
                },
            }],
            "props": {},
        })

    # ── 正文段落（按换行拆分，每行一个段落）──
    for line in (body or "").split("\n"):
        paragraphs.append({
            "elems": [{
                "type": 1,  # ELEM_TYPE_TEXT
                "text": {"text": line},
            }],
            "props": {},
        })

    return json.dumps({"paragraphs": paragraphs}, ensure_ascii=False)


def send_with_image(channel_id: str, text: str, image_path: str):
    """发送图文帖子到帖子频道。

    策略（按优先级）：
    1. 上传图片到 QQ CDN → 用 format=4 (JSON RichText) 发帖，图片用 ImageElem.third_url
    2. 上传失败 → 降级为纯文本帖子（format=1）
    """
    # 尝试上传图片到 QQ，获取 QQ 内部图片 URL
    image_url = _upload_image_to_qq(channel_id, image_path)

    title, body = _build_title_and_body(text)

    if image_url:
        # 用 JSON RichText 格式，正文 + 图片
        richtext_content = _build_richtext_json(body, image_url)
        _log("INFO", f"sending thread with image: title={title[:30]} image_url={image_url[:80]}")
        return requests.put(
            f"{BOT_API_BASE}/channels/{channel_id}/threads",
            headers={
                **auth_headers(),
                "Content-Type": "application/json",
            },
            json={
                "title": title,
                "content": richtext_content,
                "format": 4,  # FORMAT_JSON (RichText)
            },
            timeout=15,
        )
    else:
        # 图片上传失败，降级为纯文本帖子
        _log("WARN", f"image upload failed, fallback to text-only thread")
        return send_text(channel_id, text)


def _upload_image_to_qq(channel_id: str, image_path: str) -> str | None:
    """上传图片并获取可在帖子中使用的图片 URL。

    策略（按优先级）：
    1. POST /channels/{channel_id}/messages 上传 file_image，提取返回的 attachment URL
       （即使在帖子频道，QQ 可能仍返回 attachment；会产生一条内容为空格的消息副作用）
    2. 如果上述方式失败，返回 None，由调用方降级为纯文本
    """
    try:
        with open(image_path, "rb") as f:
            files = {
                "file_image": (os.path.basename(image_path) or "image.jpg", f, "image/jpeg"),
            }
            data = {
                "content": " ",  # 最少需要一个字符
            }
            resp = requests.post(
                f"{BOT_API_BASE}/channels/{channel_id}/messages",
                headers=auth_headers(),
                data=data,
                files=files,
                timeout=30,
            )
        _log("INFO", f"image upload response: status={resp.status_code} body={resp.text[:500]}")
        if resp.ok:
            body = resp.json()
            # 从返回的 attachments 中提取图片 URL
            attachments = body.get("attachments") or []
            if attachments and isinstance(attachments, list):
                url = attachments[0].get("url") or ""
                if url:
                    if not url.startswith("http"):
                        url = "https://" + url
                    _log("INFO", f"image uploaded to QQ CDN: {url}")
                    return url
            _log("WARN", f"image upload response has no attachment URL")
        else:
            _log("WARN", f"image upload to QQ failed: status={resp.status_code}")
    except Exception as e:
        _log("WARN", f"image upload exception: {e}")

    # 备用方案：尝试通过免费图床上传
    imgbb_url = _upload_image_to_imgbb(image_path)
    if imgbb_url:
        return imgbb_url

    return None


def _upload_image_to_imgbb(image_path: str) -> str | None:
    """备用图床：使用 imgbb 免费 API 上传图片。

    imgbb 免费账户无需 API key 也可上传（使用匿名上传）。
    如果 config.yaml 配置了 imgbb_api_key 则使用，否则使用匿名。
    """
    import base64

    try:
        api_key = str(cfg_get("qq.imgbb_api_key", "")).strip()

        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

        payload = {"image": image_data}
        if api_key:
            payload["key"] = api_key
            upload_url = "https://api.imgbb.com/1/upload"
        else:
            # 无 key 时跳过 imgbb
            _log("INFO", "no imgbb_api_key configured, skip imgbb upload")
            return None

        resp = requests.post(upload_url, data=payload, timeout=30)
        if resp.ok:
            data = resp.json().get("data", {})
            url = data.get("url") or data.get("display_url") or ""
            if url:
                _log("INFO", f"image uploaded to imgbb: {url}")
                return url
        _log("WARN", f"imgbb upload failed: status={resp.status_code} body={resp.text[:200]}")
    except Exception as e:
        _log("WARN", f"imgbb upload exception: {e}")
    return None


def normalize_forward_text(text: str) -> str:
    """转发前文案清洗 —— 按 transforms.yaml 里的规则依次执行。

    执行逻辑：
    1. 遍历 _TRANSFORMS 列表，对每条规则：
       - regex_replace：re.sub(compiled, repl, text)
       - append：在文末追加固定文本
    2. 内置收尾：多空行收敛 (≥3 个换行→2 个) + 首尾去空白

    规则在 worker 启动时一次性加载并预编译，修改 YAML 后只需重启 worker。
    """
    if not text:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")

    for rule in _TRANSFORMS:
        rtype = rule["type"]
        if rtype == "regex_replace":
            t = rule["compiled"].sub(rule["repl"], t)
        elif rtype == "append":
            # 追加前先去尾部空白，追加后保证以换行分隔
            t = t.rstrip()
            if t:
                append_text = rule["text"]
                # YAML 的 literal block (|) 会保留尾部换行，strip 一下
                t += "\n\n" + append_text.strip()

    # 内置：多空行收敛
    t = _RE_MULTI_NEWLINE.sub("\n\n", t)

    # 去首尾空白
    t = t.strip()

    return t


# 启动 WS 在线保活（后台线程）
_keepalive = QQWsKeepAlive()
_keepalive.start()

# ── 首次启动：等待 WS 就绪（最多等 120s，避免 WS 没 ready 就开始发消息全部失败）──
_log("INFO", "waiting for QQ WS to be ready before processing queue...")
if _keepalive.wait_until_ready(timeout=120):
    _log("INFO", "QQ WS is ready, start processing queue")
else:
    _log("WARN", f"QQ WS not ready after 120s (err={_keepalive.last_error}), will process queue anyway")

while True:
    # ── 静默时段：QQ 频道 00:00~06:00 禁止主动消息 ──
    # 消息留在 Redis 队列，时段结束后自动恢复发送
    if _in_quiet_hours():
        _log("INFO", f"quiet hours ({QUIET_HOURS_START}:00~{QUIET_HOURS_END}:00), pausing queue consumption...")
        while _in_quiet_hours():
            time.sleep(60)  # 每分钟检查一次
        _log("INFO", "quiet hours ended, resuming queue consumption")

    # ── WS 不在线时，不从队列取消息，阻塞等待 ──
    # 这样消息安全留在 Redis 里，WS 恢复后按顺序发出，不会进死信
    if not _keepalive.ready:
        _log("WARN", f"QQ WS not ready, pausing queue consumption... err={_keepalive.last_error}")
        while not _keepalive.ready:
            _keepalive.wait_until_ready(timeout=60)
            if not _keepalive.ready:
                _log("WARN", f"QQ WS still not ready, keep waiting... err={_keepalive.last_error}")
        _log("INFO", "QQ WS recovered, resuming queue consumption")

    _, raw = r.brpop("queue")
    task = json.loads(raw)

    chat_id = int(task["chat_id"])
    msg_id = int(task["msg_id"])

    # 纯 env 模式：优先用运行时自动选择到的 DEFAULT_SEND_CHANNEL_ID；否则回退任务内携带的 qq_channel_id
    qq_channel_id = DEFAULT_SEND_CHANNEL_ID or str(task.get("qq_channel_id") or "")

    if not qq_channel_id:
        save_dead(chat_id, msg_id, "missing QQ target channel_id (QQ_TARGET_CHANNEL_ID empty)", task)
        _log("ERROR", f"drop to dead: missing target channel_id. chat_id={chat_id} msg_id={msg_id}")
        time.sleep(1.0)
        continue

    # 模板处理
    content = apply_template(
        task.get("text", ""),
        task.get("template"),
        {"channel_name": task.get("channel_name", "")},
    )

    # 发送前文本规范化（按你的业务清洗规则）
    content = normalize_forward_text(content)

    success = False
    err = None

    try:
        resp = None

        def _do_send_once() -> tuple[bool, requests.Response | None]:
            if task.get("media"):
                media_path = task["media"]
                # 图片文件不存在时（死信重发、容器重启后 /tmp 清空），降级为纯文字
                if not os.path.exists(media_path):
                    _log("WARN", f"media file missing, fallback to text-only: {media_path}")
                else:
                    r1 = send_with_image(qq_channel_id, content, media_path)
                    if r1.ok:
                        return True, r1

                    # 原图发送失败，尝试压缩后重试
                    compressed = compress_image(media_path)
                    if compressed:
                        r2 = send_with_image(qq_channel_id, content, compressed)
                        if r2.ok:
                            return True, r2

                # 图片发送全部失败，降级为纯文本
                r3 = send_text(qq_channel_id, content)
                return bool(r3.ok), r3

            r0 = send_text(qq_channel_id, content)
            return bool(r0.ok), r0

        success, resp = _do_send_once()

        # ── 限流检测：QQ 频道消息发送达到上限 → 暂停等待 ──
        if not success and resp is not None and _is_rate_limited(resp):
            _log("WARN", "QQ channel message rate limit hit! pushing back to queue, sleeping 300s...")
            # 把这条消息推回队列头部，不进死信
            r.rpush("queue", json.dumps(task, ensure_ascii=False))
            time.sleep(300)  # 等 5 分钟再继续
            continue

        # 失败时：鉴权/在线问题 → 强制刷新 token + 等待 WS ready → 再试一次
        if not success and resp is not None:
            text_blob = None
            try:
                text_blob = resp.text
            except Exception:
                text_blob = None

            if _is_auth_error(resp, text_blob) or _is_online_required_error(resp, text_blob):
                _log("WARN", f"send failed, will retry once after refresh/ws. status={resp.status_code} body={text_blob}")
                _log("INFO", f"token_status(before)={get_token_status()} ws_ready={_keepalive.ready} ws_err={_keepalive.last_error}")

                # 强制刷新一次 token（如果拿不到新 token，会继续使用旧 token）
                _ = auth_headers(force_refresh=True)

                # 等待 WS ready（短等待，避免阻塞太久）
                _keepalive.wait_until_ready()

                success, resp = _do_send_once()

        if not success and resp is not None and err is None:
            try:
                err = f"http {resp.status_code}: {resp.text}"
            except Exception:
                err = f"http {resp.status_code}"

    except Exception as e:
        err = str(e)
        # 最后兜底：能发文字就发文字
        try:
            resp = send_text(qq_channel_id, content)
            success = bool(resp.ok)
            if not success and err is None:
                err = f"http {resp.status_code}: {resp.text}"
        except Exception as e2:
            err = err or str(e2)
            success = False

    if success:
        mark_processed(chat_id, msg_id)
        _log("INFO", f"sent ok. chat_id={chat_id} msg_id={msg_id} channel_id={qq_channel_id}")
    else:
        save_dead(chat_id, msg_id, err or "send failed", task)
        _log("ERROR", f"sent failed -> dead. chat_id={chat_id} msg_id={msg_id} channel_id={qq_channel_id} err={err}")

    # 清理临时媒体文件，避免 /tmp 积压
    if task.get("media"):
        for f in (task["media"], task["media"].replace(".jpg", "_compressed.jpg")):
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

    # 防风控：宁慢勿快
    time.sleep(max(SEND_INTERVAL, 0.2))
