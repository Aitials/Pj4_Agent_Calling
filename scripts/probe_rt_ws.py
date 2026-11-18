"""一知 AICC 实时通道探测（WebSocket 直连，Socket.IO 2.x / Engine.IO v3）。

为什么用 WS：
- 握手时服务端 upgrades 含 websocket，直连后**零 HTTP 请求**，比 polling 更省、更稳；
- 心跳包（2/3）在 WS 上收发可见，便于确认会话是否存活；
- polling 模式下服务端关闭会话时只会断流，难以判断原因。

用法:
    python scripts/probe_rt_ws.py --seconds 300
    python scripts/probe_rt_ws.py --seconds 1200 --channels scrmio
    python scripts/probe_rt_ws.py --seconds 60 --raw          # 打印所有原始包

输出: state/probe/realtime/ws_<channel>_<ts>.jsonl / ws_summary_<ts>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path

import websockets

BASE = "https://aicc.tanyibot.com"
HOST = "aicc.tanyibot.com"
OUT_DIR = Path("state/probe/realtime")
CHANNELS = {"socketio": "/socket.io", "scrmio": "/scrmio"}
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)


def load_cookie() -> str:
    cookie = os.environ.get("AICC_COOKIE", "").strip()
    if cookie:
        return cookie
    f = Path("state/probe/cookie.txt")
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    env = Path(".env")
    if env.exists():
        m = re.search(r"^AICC_COOKIE=(.*)$", env.read_text(encoding="utf-8"), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    raise SystemExit("缺少 Cookie")


def token_from_cookie(cookie: str) -> str:
    m = re.search(r"(?:^|;\s*)token=([^;]+)", cookie)
    if not m:
        raise SystemExit("Cookie 无 token")
    return m.group(1)


def parse_packet(pkt: str) -> dict:
    info: dict = {"raw": pkt}
    if not pkt:
        return info
    eio, body = pkt[0], pkt[1:]
    if eio == "0":
        info["kind"] = "open"
        try:
            info["handshake"] = json.loads(body)
        except Exception:
            info["body"] = body
    elif eio in ("1", "2", "3"):
        info["kind"] = {"1": "close", "2": "ping", "3": "pong"}[eio]
        if body:
            info["body"] = body
    elif eio == "4":
        sio, rest = (body[:1], body[1:])
        info["kind"] = {"0": "connect", "1": "disconnect", "2": "event", "3": "ack", "4": "error"}.get(
            sio, f"sio_{sio}"
        )
        if sio == "2":
            try:
                arr = json.loads(rest)
                if isinstance(arr, list) and arr:
                    info["event"] = arr[0]
                    info["args"] = arr[1:]
            except Exception:
                info["rawEvent"] = rest
        elif rest:
            info["body"] = rest
    else:
        info["kind"] = "unknown"
    return info


class Watcher:
    def __init__(
        self, name: str, path: str, token: str, cookie: str, deadline: float, raw: bool, client_ping: int
    ) -> None:
        self.name = name
        self.path = path
        self.token = token
        self.cookie = cookie
        self.deadline = deadline
        self.raw = raw
        self.client_ping = client_ping
        self.ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.out = OUT_DIR / f"ws_{name}_{self.ts}.jsonl"
        self.counts: dict[str, int] = {}
        self.events: dict[str, dict] = {}
        self.reconnects = 0
        self.error: str | None = None
        self.alive_seconds = 0.0

    def url(self) -> str:
        return f"wss://{HOST}{self.path}/?EIO=3&transport=websocket&token={self.token}"

    def log(self, item: dict) -> None:
        item["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        item["channel"] = self.name
        with self.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    async def run_once(self) -> bool:
        """返回是否需要重连。"""
        headers = {"Origin": BASE, "User-Agent": UA, "Cookie": self.cookie, "Accept": "*/*"}
        async with websockets.connect(
            self.url(), additional_headers=headers, open_timeout=25, max_size=8 * 1024 * 1024
        ) as ws:
            started = asyncio.get_event_loop().time()
            self.log({"phase": "ws_open", "subprotocol": ws.subprotocol})
            last_in = started
            inflight = 0
            while asyncio.get_event_loop().time() < self.deadline:
                # 客户端主动保活：30 秒内没收到任何包就发一次 ping（2）
                now = asyncio.get_event_loop().time()
                if self.client_ping and now - last_in > self.client_ping:
                    await ws.send("2")
                    self.bump("client_ping")
                    self.log({"phase": "send", "packet": "2(客户端ping)"})
                    last_in = now
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    continue
                except websockets.ConnectionClosed as exc:
                    self.log({"phase": "closed", "code": exc.code, "reason": str(exc.reason)[:120]})
                    return exc.code not in (1000,)
                last_in = asyncio.get_event_loop().time()
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", "ignore")
                if msg == "2":
                    await ws.send("3")
                    self.bump("server_ping")
                    self.log({"phase": "recv", "parsed": {"kind": "ping"}})
                    continue
                if msg[:1] == "2" and len(msg) > 1:
                    await ws.send("3" + msg[1:])
                    self.bump("server_ping_probe")
                    continue
                if msg == "3":
                    self.bump("pong_ack")
                    self.log({"phase": "recv", "parsed": {"kind": "pong"}})
                    continue
                parsed = parse_packet(msg)
                kind = parsed.get("kind", "?")
                self.bump(kind)
                if kind == "event":
                    ev = str(parsed.get("event"))
                    self.bump(f"event:{ev}")
                    rec = self.events.setdefault(
                        ev, {"event": ev, "count": 0, "firstTs": None, "samples": []}
                    )
                    rec["count"] += 1
                    rec["firstTs"] = rec["firstTs"] or datetime.now().strftime("%H:%M:%S")
                    if len(rec["samples"]) < 5:
                        rec["samples"].append(parsed.get("args"))
                    self.log({"phase": "EVENT", "parsed": parsed})
                elif kind == "close":
                    self.log({"phase": "recv", "parsed": parsed})
                    self.error = "服务端下发 close(1)"
                    return True
                elif self.raw or kind in ("open", "connect", "disconnect", "error"):
                    self.log({"phase": "recv", "parsed": parsed})
            self.alive_seconds = asyncio.get_event_loop().time() - started
            return False

    async def run(self) -> None:
        attempt = 0
        while asyncio.get_event_loop().time() < self.deadline and attempt <= 3:
            try:
                need_reconnect = await self.run_once()
            except Exception as exc:  # noqa: BLE001
                self.log({"phase": "error", "attempt": attempt + 1, "error": f"{type(exc).__name__}: {exc}"})
                need_reconnect = True
            if not need_reconnect:
                return
            attempt += 1
            if attempt > 3:
                self.error = self.error or "重连次数用尽"
                return
            self.reconnects += 1
            backoff = 15 * attempt  # 15s / 30s / 45s，绝不空转
            self.log({"phase": "reconnect_wait", "attempt": attempt, "backoff": backoff})
            await asyncio.sleep(min(backoff, max(0, self.deadline - asyncio.get_event_loop().time())))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--channels", default="socketio,scrmio")
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--client-ping", type=int, default=0, help="客户端主动 ping 间隔秒，0=不主动发")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cookie = load_cookie()
    token = token_from_cookie(cookie)
    deadline = asyncio.get_event_loop().time() + args.seconds
    print(f"[ws] 通道={args.channels} 时长={args.seconds}s")

    ws = [
        Watcher(n.strip(), CHANNELS[n.strip()], token, cookie, deadline, args.raw, args.client_ping)
        for n in args.channels.split(",")
        if n.strip() in CHANNELS
    ]
    # 先后建连，避免同时打
    tasks = []
    for i, w in enumerate(ws):
        await asyncio.sleep(2 * i)
        tasks.append(asyncio.create_task(w.run()))
    await asyncio.gather(*tasks)

    summary = {"seconds": args.seconds, "channels": {}}
    for w in ws:
        summary["channels"][w.name] = {
            "aliveSeconds": round(w.alive_seconds, 1),
            "reconnects": w.reconnects,
            "error": w.error,
            "counts": w.counts,
            "events": w.events,
        }
    (OUT_DIR / f"ws_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
