import os
import json
import time
import requests
import redis
from PIL import Image
import re

from db import mark_processed, save_dead

from qq_auth import auth_headers, get_token_status
from qq_ws_keepalive import QQWsKeepAlive

r = redis.Redis(host=os.getenv("REDIS_HOST"), decode_responses=True)

BOT_API_BASE = os.getenv("QQ_API_BASE", "https://api.sgroup.qq.com").rstrip("/")

# 纯 env 模式：可只填 guild_id，让程序自动选择第一个“文字子频道”作为发送目标
QQ_TARGET_CHANNEL_ID = (os.getenv("QQ_TARGET_CHANNEL_ID") or "").strip()
QQ_TARGET_GUILD_ID = (os.getenv("QQ_TARGET_GUILD_ID") or "").strip()


def _log(level: str, msg: str):
    # 简单结构化，方便 docker logs grep
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][{level}] {msg}")


# 防风控：发送间隔（秒）
try:
    SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "1.5"))
except Exception:
    SEND_INTERVAL = 1.5


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

ZJ_BASE_URL = os.getenv("ZJ_BASE_URL", "www.zhuiju.us")
ZJ_SUFFIX_NOTE = os.getenv("ZJ_SUFFIX_NOTE", "访问搜影片名或进QQ群搜索")


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


def send_text(channel_id: str, text: str):
    return requests.post(
        f"{BOT_API_BASE}/channels/{channel_id}/messages",
        headers={
            **auth_headers(),
            "Content-Type": "application/json",
        },
        json={"content": text},
        timeout=15,
    )


def send_with_image(channel_id: str, text: str, image_path: str):
    """真实图文：使用 multipart/form-data 的 file_image 上传。

    文档：POST /channels/{channel_id}/messages
    - content: 文本
    - file_image: 文件

    注意：requests 会自动设置 multipart boundary，所以不要手动写 Content-Type。
    """
    with open(image_path, "rb") as f:
        files = {
            # (filename, fileobj, mimetype)
            "file_image": (os.path.basename(image_path) or "image.jpg", f, "image/jpeg"),
        }
        data = {
            "content": text or "",
        }
        return requests.post(
            f"{BOT_API_BASE}/channels/{channel_id}/messages",
            headers=auth_headers(),
            data=data,
            files=files,
            timeout=30,
        )


def normalize_forward_text(text: str) -> str:
    """转发前文案清洗规则：

    1) 删除：来自/频道/群组/投稿 及其后所有内容（含该行前可能的 emoji 图标）。
    2) 仅对“网盘链接那一行”做替换：
       - 行内含 pan.quark.cn/s/xxx 时：
         * 将该 URL 替换为 ZJ_BASE_URL，并追加备注
         * 将该行的前缀关键词“夸克/链接/网盘资源链接”等统一规范为“网盘资源链接：”
       - 不进行全文范围的“夸克”替换（避免误伤影片名/描述）。

    说明：只处理文字，不改图片。
    """
    if not text:
        return ""

    t = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = t.split("\n")
    out_lines: list[str] = []

    cut_re = re.compile(r"^\s*(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\s*)?(来自|频道|群组|投稿)\s*[:：]")

    quark_url_re = re.compile(r"https?://pan\.quark\.cn/s/[A-Za-z0-9]+", re.IGNORECASE)

    # “网盘链接行”的前缀识别：兼容“夸克：”“链接：”“网盘资源链接：”以及没有冒号但有空格的写法
    link_prefix_re = re.compile(r"^\s*(?:夸克|链接|网盘资源链接)\s*[:：]?\s*", re.IGNORECASE)

    for line in lines:
        if cut_re.search(line):
            break

        if quark_url_re.search(line):
            # 1) 替换 URL
            line = quark_url_re.sub(ZJ_BASE_URL, line)

            # 2) 规范前缀为“网盘资源链接：”
            line2 = link_prefix_re.sub("网盘资源链接：", line)
            # 如果原本没有前缀（比如直接写 URL），也补上
            if line2 == line:
                line2 = f"网盘资源链接：{line}".strip()
            line = line2

            # 3) 追加说明（避免重复追加）
            if ZJ_BASE_URL in line and ZJ_SUFFIX_NOTE and ZJ_SUFFIX_NOTE not in line:
                line = f"{line} {ZJ_SUFFIX_NOTE}".rstrip()

        out_lines.append(line)

    while out_lines and out_lines[-1].strip() == "":
        out_lines.pop()

    return "\n".join(out_lines).strip()


# 启动 WS 在线保活（后台线程）
_keepalive = QQWsKeepAlive()
_keepalive.start()

while True:
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
                r1 = send_with_image(qq_channel_id, content, task["media"])
                if r1.ok:
                    return True, r1

                compressed = compress_image(task["media"])
                if compressed:
                    r2 = send_with_image(qq_channel_id, content, compressed)
                    if r2.ok:
                        return True, r2

                r3 = send_text(qq_channel_id, content)
                return bool(r3.ok), r3

            r0 = send_text(qq_channel_id, content)
            return bool(r0.ok), r0

        success, resp = _do_send_once()

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

    # 防风控：宁慢勿快
    time.sleep(max(SEND_INTERVAL, 0.2))
