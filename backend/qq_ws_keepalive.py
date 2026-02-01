import os
import json
import time
import threading
from typing import Optional

import requests

try:
    import websocket  # websocket-client
except Exception as e:  # pragma: no cover
    websocket = None

from qq_auth import get_access_token

BOT_API_BASE = os.getenv("QQ_API_BASE", "https://api.sgroup.qq.com").rstrip("/")

QQ_WS_INTENTS = int(os.getenv("QQ_WS_INTENTS", str(1 << 0)))

# 观测与等待
QQ_WS_LOG_INTERVAL_SECONDS = int(os.getenv("QQ_WS_LOG_INTERVAL_SECONDS", "60"))
QQ_WS_READY_TIMEOUT_SECONDS = int(os.getenv("QQ_WS_READY_TIMEOUT_SECONDS", "20"))


def _get_gateway_url() -> str:
    resp = requests.get(
        f"{BOT_API_BASE}/gateway",
        headers={"Authorization": f"QQBot {get_access_token()}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"invalid gateway response: {data}")
    return url


class QQWsKeepAlive:
    """最小 WebSocket 在线保活。

    目标：满足“发送频道消息要求机器人接口需要连接到 websocket 上保持在线状态”。
    - 连接 gateway
    - 收到 Hello(op=10) 后按 heartbeat_interval 发送心跳(op=1)
    - Identify(op=2) 登录，token 需要 "QQBot {AccessToken}"

    说明：官方文档提示 websocket 链路可能逐步下线；如果后续不可用，需要迁移 webhook。
    """

    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._ws = None
        self._heartbeat_interval = 45.0
        self._last_seq = None

        self._ready = threading.Event()
        self._last_heartbeat_at: float = 0.0
        self._last_connect_at: float = 0.0
        self._last_error: str | None = None
        self._connected_url: str | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_heartbeat_at(self) -> float:
        return self._last_heartbeat_at

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        timeout = QQ_WS_READY_TIMEOUT_SECONDS if timeout is None else timeout
        return self._ready.wait(timeout=timeout)

    def start(self):
        if websocket is None:
            raise RuntimeError("websocket-client not installed")
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="qq-ws-keepalive", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._ready.clear()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _send(self, payload: dict):
        if not self._ws:
            return
        self._ws.send(json.dumps(payload, ensure_ascii=False))

    def _identify(self):
        token = get_access_token()
        self._send(
            {
                "op": 2,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": QQ_WS_INTENTS,
                    "shard": [0, 1],
                    "properties": {
                        "$os": os.name,
                        "$browser": "tg2qqpd",
                        "$device": "tg2qqpd",
                    },
                },
            }
        )

    def _heartbeat_loop(self):
        while not self._stop.is_set():
            self._send({"op": 1, "d": self._last_seq})
            self._last_heartbeat_at = time.time()
            time.sleep(float(self._heartbeat_interval))

    def _log_loop(self):
        while not self._stop.is_set():
            if self.ready:
                age = int(time.time() - (self._last_heartbeat_at or time.time()))
                print(
                    f"[qq-ws] ready url={self._connected_url} seq={self._last_seq} "
                    f"heartbeat_interval={self._heartbeat_interval:.1f}s last_hb_age={age}s"
                )
            else:
                err = self._last_error or "(none)"
                print(f"[qq-ws] not-ready last_error={err}")
            time.sleep(max(QQ_WS_LOG_INTERVAL_SECONDS, 5))

    def _run(self):
        backoff = 2
        log_thread = threading.Thread(target=self._log_loop, name="qq-ws-log", daemon=True)
        log_thread.start()

        while not self._stop.is_set():
            try:
                self._ready.clear()
                self._last_error = None

                url = _get_gateway_url()
                self._connected_url = url

                self._ws = websocket.create_connection(url, timeout=20)
                self._ws.settimeout(60)
                self._last_connect_at = time.time()

                hello_raw = self._ws.recv()
                hello = json.loads(hello_raw)
                if int(hello.get("op", -1)) != 10:
                    raise RuntimeError(f"expected hello(op=10), got: {hello}")

                interval_ms = (hello.get("d") or {}).get("heartbeat_interval")
                self._heartbeat_interval = max(float(interval_ms or 45000) / 1000.0, 5.0)

                self._identify()

                hb_thread = threading.Thread(target=self._heartbeat_loop, name="qq-ws-heartbeat", daemon=True)
                hb_thread.start()

                backoff = 2

                while not self._stop.is_set():
                    raw = self._ws.recv()
                    msg = json.loads(raw)
                    op = msg.get("op")
                    s = msg.get("s")
                    if s is not None:
                        self._last_seq = s

                    # READY 到来，标记可用
                    if op == 0 and msg.get("t") == "READY":
                        self._ready.set()

                    if op == 7:  # Reconnect
                        raise RuntimeError("gateway requested reconnect")
                    if op == 9:  # Invalid Session
                        raise RuntimeError(f"invalid session: {msg}")
                    # op=11 Heartbeat ACK / op=0 Dispatch / etc. 保活无需处理

            except Exception as e:
                self._last_error = str(e)
                self._ready.clear()
                try:
                    if self._ws:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None

                # 简单指数退避
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
