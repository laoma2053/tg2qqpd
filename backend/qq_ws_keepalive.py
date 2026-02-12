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
from config import get as cfg_get

BOT_API_BASE = str(cfg_get("qq.api_base", "https://api.sgroup.qq.com")).rstrip("/")

QQ_WS_INTENTS = int(cfg_get("qq.ws_intents", 1))

# 观测与等待
QQ_WS_LOG_INTERVAL_SECONDS = int(cfg_get("qq.ws_log_interval_seconds", 60))
QQ_WS_READY_TIMEOUT_SECONDS = int(cfg_get("qq.ws_ready_timeout_seconds", 20))


def _get_gateway_url() -> tuple[str, dict]:
    """获取 gateway URL，同时返回 session_start_limit 信息。"""
    resp = requests.get(
        f"{BOT_API_BASE}/gateway/bot",
        headers={"Authorization": f"QQBot {get_access_token()}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        raise RuntimeError(f"invalid gateway response: {data}")
    limit = data.get("session_start_limit") or {}
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}][INFO] [qq-ws] gateway: url={url} shards={data.get('shards')} limit={limit}")
    return url, limit


class QQWsKeepAlive:
    """最小 WebSocket 在线保活。

    目标：满足"发送频道消息要求机器人接口需要连接到 websocket 上保持在线状态"。
    - 连接 gateway
    - 收到 Hello(op=10) 后按 heartbeat_interval 发送心跳(op=1)
    - Identify(op=2) 登录，token 需要 "QQBot {AccessToken}"

    保护机制：
    - 连接配额保护：remaining=0 时等待 reset_after 再连
    - 连续失败熔断：连续 N 次失败（未曾 READY）触发长休眠，防止刷光配额
    - 指数退避：5s → 10s → 20s → 40s → 60s（封顶）

    说明：官方文档提示 websocket 链路可能逐步下线；如果后续不可用，需要迁移 webhook。
    """

    # 连续失败多少次后触发熔断（进入长休眠）
    CIRCUIT_BREAKER_THRESHOLD = 5
    # 熔断后休眠多少秒（默认 30 分钟）
    CIRCUIT_BREAKER_SLEEP = 1800

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

        # 连续失败计数器（每次 READY 成功后归零）
        self._consecutive_failures = 0

        # 每次连接独立的心跳退出信号，防止旧线程用已关闭的 socket 发心跳
        self._hb_stop: threading.Event = threading.Event()

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
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}][INFO] [qq-ws] identify: intents={QQ_WS_INTENTS} token={token[:8]}...{token[-4:]}")
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

    def _heartbeat_loop(self, conn_stop: threading.Event):
        """每个连接独立的心跳线程；conn_stop 被 set 后立即退出，避免用旧 socket 发心跳。"""
        while not self._stop.is_set() and not conn_stop.is_set():
            try:
                self._send({"op": 1, "d": self._last_seq})
                self._last_heartbeat_at = time.time()
            except Exception:
                break  # socket 已关闭，直接退出
            # 用 wait 代替 sleep，这样 conn_stop.set() 后能立即退出
            conn_stop.wait(timeout=float(self._heartbeat_interval))

    def _log_loop(self):
        # 限频输出：默认每 60s 打印一次（由 QQ_WS_LOG_INTERVAL_SECONDS 控制）
        while not self._stop.is_set():
            try:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                if self.ready:
                    age = int(time.time() - (self._last_heartbeat_at or time.time()))
                    print(
                        f"[{ts}][INFO] [qq-ws] ready url={self._connected_url} seq={self._last_seq} "
                        f"heartbeat_interval={self._heartbeat_interval:.1f}s last_hb_age={age}s"
                    )
                else:
                    err = self._last_error or "(none)"
                    print(f"[{ts}][WARN] [qq-ws] not-ready last_error={err}")
            except Exception:
                # 永不让日志线程崩
                pass

            # 至少 5s，但默认 60s（避免刷屏）
            time.sleep(max(QQ_WS_LOG_INTERVAL_SECONDS, 5))

    def _run(self):
        backoff = 5
        log_thread = threading.Thread(target=self._log_loop, name="qq-ws-log", daemon=True)
        log_thread.start()

        while not self._stop.is_set():
            # ── 熔断检查：连续失败 N 次 → 长休眠，防止刷光配额 ──
            if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[{ts}][ERROR] [qq-ws] CIRCUIT BREAKER: "
                    f"{self._consecutive_failures} consecutive failures, "
                    f"sleeping {self.CIRCUIT_BREAKER_SLEEP}s ({self.CIRCUIT_BREAKER_SLEEP//60}min). "
                    f"last_error={self._last_error}"
                )
                self._last_error = (
                    f"circuit breaker: {self._consecutive_failures} failures, "
                    f"sleeping {self.CIRCUIT_BREAKER_SLEEP//60}min"
                )
                self._stop.wait(timeout=self.CIRCUIT_BREAKER_SLEEP)
                if self._stop.is_set():
                    break
                # 休眠结束后重置计数器和退避，给一轮新机会
                self._consecutive_failures = 0
                backoff = 5
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}][INFO] [qq-ws] circuit breaker reset, retrying...")

            # 通知上一轮心跳线程退出
            self._hb_stop.set()
            conn_stop = threading.Event()
            self._hb_stop = conn_stop

            try:
                self._ready.clear()
                self._last_error = None

                url, limit = _get_gateway_url()
                self._connected_url = url

                # ── 连接配额保护 ──
                remaining = int(limit.get("remaining", 999))
                reset_after_ms = int(limit.get("reset_after", 0))
                if remaining <= 0:
                    wait_sec = max(reset_after_ms / 1000.0, 60) + 5  # 多等 5 秒余量
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(
                        f"[{ts}][WARN] [qq-ws] session_start_limit exhausted! "
                        f"remaining=0, will wait {wait_sec:.0f}s for reset"
                    )
                    self._last_error = f"rate limited, waiting {wait_sec:.0f}s"
                    self._stop.wait(timeout=wait_sec)
                    continue

                # ── 配额低警告 ──
                if remaining < 20:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[{ts}][WARN] [qq-ws] session_start_limit low: remaining={remaining}")

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

                hb_thread = threading.Thread(
                    target=self._heartbeat_loop, args=(conn_stop,),
                    name="qq-ws-heartbeat", daemon=True,
                )
                hb_thread.start()

                backoff = 5

                while not self._stop.is_set():
                    raw = self._ws.recv()
                    msg = json.loads(raw)
                    op = msg.get("op")
                    s = msg.get("s")
                    t = msg.get("t")
                    if s is not None:
                        self._last_seq = s

                    # READY 到来，标记可用
                    if op == 0 and t == "READY":
                        self._ready.set()
                        self._consecutive_failures = 0  # ★ 成功连接，重置失败计数
                        ts_now = time.strftime("%Y-%m-%d %H:%M:%S")
                        print(f"[{ts_now}][INFO] [qq-ws] READY! session established")

                    if op == 11:
                        pass  # Heartbeat ACK，正常
                    elif op == 7:  # Reconnect（服务端要求重连，不算失败）
                        raise RuntimeError("gateway requested reconnect")
                    elif op == 9:  # Invalid Session
                        raise RuntimeError(f"invalid session: {msg}")

            except Exception as e:
                err_str = str(e)
                self._last_error = err_str
                self._ready.clear()
                conn_stop.set()  # 通知本轮心跳线程立即退出
                try:
                    if self._ws:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None

                # 如果是 gateway reconnect（op=7），不计入连续失败
                is_reconnect = "gateway requested reconnect" in err_str
                if not is_reconnect:
                    self._consecutive_failures += 1

                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[{ts}][WARN] [qq-ws] connection failed: {e}, "
                    f"consecutive_failures={self._consecutive_failures}, retry in {backoff}s"
                )

                # 指数退避：5 → 10 → 20 → 40 → 60（封顶）
                self._stop.wait(timeout=backoff)
                backoff = min(backoff * 2, 60)
