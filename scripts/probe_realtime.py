"""一知 AICC 两条实时通道探测（Socket.IO 2.x / Engine.IO v3，polling 传输）。

用途：被动监听 /socket.io/（全局 AICC）与 /scrmio/（会员中心）实际收到的业务事件与 payload。
特点：每条通道只建 1 条连接，完全按服务端 pingInterval 节流，不发任何业务请求。

用法:
    python scripts/probe_realtime.py --seconds 600
    python scripts/probe_realtime.py --seconds 300 --channels scrmio

原始包: state/probe/realtime/<channel>_<ts>.jsonl
事件汇总: state/probe/realtime/summary_<ts>.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE = "https://aicc.tanyibot.com"
OUT_DIR = Path("state/probe/realtime")
CHANNELS = {
    # 名称 -> Engine.IO 挂载路径（不带尾部斜杠）
    "socketio": "/socket.io",
    "scrmio": "/scrmio",
}
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
    raise SystemExit("缺少 Cookie：设置 AICC_COOKIE / .env / state/probe/cookie.txt")


def token_from_cookie(cookie: str) -> str:
    m = re.search(r"(?:^|;\s*)token=([^;]+)", cookie)
    if not m:
        raise SystemExit("Cookie 中找不到 token 字段")
    return m.group(1)


def decode_payload(body: str) -> list[str]:
    """Engine.IO v3 polling payload: <len>:<packet><len>:<packet>..."""
    packets: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        colon = body.find(":", i)
        if colon == -1:
            # 兜底：非长度前缀格式（个别网关会直接返回裸包）
            packets.append(body[i:])
            break
        try:
            length = int(body[i:colon])
        except ValueError:
            packets.append(body[i:])
            break
        start = colon + 1
        packets.append(body[start : start + length])
        i = start + length
    return [p for p in packets if p]


def encode_payload(packet: str) -> str:
    return f"{len(packet)}:{packet}"


def parse_socketio_packet(packet: str) -> dict:
    """解析 Engine.IO 包 + Socket.IO 2.x 包，返回结构化描述。"""
    info: dict = {"raw": packet, "eio": packet[:1] if packet else ""}
    if not packet:
        return info
    eio = packet[0]
    body = packet[1:]
    if eio == "0":
        try:
            info["handshake"] = json.loads(body)
        except Exception:
            info["handshake"] = body
        info["kind"] = "open"
    elif eio == "1":
        info["kind"] = "close"
    elif eio == "2":
        info["kind"] = "ping"
    elif eio == "3":
        info["kind"] = "pong"
    elif eio == "4":
        sio_type = body[:1]
        rest = body[1:]
        mapping = {"0": "connect", "1": "disconnect", "2": "event", "3": "ack", "4": "error"}
        info["kind"] = mapping.get(sio_type, f"sio_{sio_type}")
        if sio_type == "2":
            try:
                arr = json.loads(rest)
                if isinstance(arr, list) and arr:
                    info["event"] = arr[0]
                    info["args"] = arr[1:]
            except Exception:
                info["event"] = None
                info["rawEvent"] = rest
        elif rest:
            info["body"] = rest
    else:
        info["kind"] = "unknown"
    return info


class ChannelWatcher(threading.Thread):
    def __init__(self, name: str, path: str, token: str, seconds: int, out: Path) -> None:
        super().__init__(daemon=True)
        self.name_ = name
        self.path = path
        self.token = token
        self.seconds = seconds
        self.out = out
        self.sid: str | None = None
        self.events: dict[str, dict] = {}
        self.counts: dict[str, int] = {}
        self.request_count = 0
        self.error: str | None = None
        self._started_at = 0.0

    def _url(self, **params: str) -> str:
        q = {"EIO": "3", "transport": "polling", "token": self.token, "b64": "1"}
        q.update(params)
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        return f"{BASE}{self.path}/?{qs}"

    def log(self, item: dict) -> None:
        item["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        item["channel"] = self.name_
        with self.out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def run(self) -> None:
        self._started_at = time.time()
        headers = {
            "Accept": "*/*",
            "Referer": f"{BASE}/active-scrm-admin/customerManagement",
            "User-Agent": UA,
            "Origin": BASE,
        }
        try:
            with httpx.Client(headers=headers, timeout=75) as client:
                # 1) 握手（偶发 401/断流时就地重试，最多 3 次，间隔 5 秒）
                info: dict = {}
                for attempt in range(3):
                    try:
                        r = client.get(self._url())
                    except Exception as exc:  # noqa: BLE001
                        self.log({"phase": "handshake", "attempt": attempt + 1, "error": str(exc)})
                        time.sleep(5)
                        continue
                    self.request_count += 1
                    packets = decode_payload(r.text)
                    info = parse_socketio_packet(packets[0]) if packets else {"raw": r.text}
                    self.log(
                        {
                            "phase": "handshake",
                            "attempt": attempt + 1,
                            "httpStatus": r.status_code,
                            "parsed": info,
                        }
                    )
                    if r.status_code == 200 and (info.get("handshake") or {}).get("sid"):
                        break
                    time.sleep(5)
                hs = info.get("handshake") or {}
                self.sid = hs.get("sid")
                ping_interval = hs.get("pingInterval", 25000) / 1000
                if not self.sid:
                    self.error = f"handshake 失败: {str(info)[:200]}"
                    return

                # 2) 长轮询收包
                while time.time() - self._started_at < self.seconds:
                    try:
                        rr = client.get(self._url(sid=self.sid))
                    except httpx.TimeoutException:
                        self.log({"phase": "poll", "note": "长轮询超时（无事件）"})
                        continue
                    except Exception as exc:  # noqa: BLE001
                        self.error = f"poll 异常: {exc}"
                        self.log({"phase": "poll", "error": str(exc)})
                        time.sleep(3)
                        continue
                    self.request_count += 1
                    if rr.status_code != 200:
                        self.log({"phase": "poll", "httpStatus": rr.status_code, "text": rr.text[:300]})
                        if rr.status_code in (400, 401, 403):
                            self.error = f"poll HTTP {rr.status_code}"
                            return
                        time.sleep(3)
                        continue

                    saw_ping = False
                    for pkt in decode_payload(rr.text):
                        parsed = parse_socketio_packet(pkt)
                        kind = parsed.get("kind")
                        if kind == "ping":
                            saw_ping = True
                            continue
                        self.counts[kind or "?"] = self.counts.get(kind or "?", 0) + 1
                        if kind == "event":
                            ev = parsed.get("event")
                            key = str(ev)
                            self.counts[f"event:{key}"] = self.counts.get(f"event:{key}", 0) + 1
                            rec = self.events.setdefault(
                                key, {"event": ev, "count": 0, "firstTs": None, "samples": []}
                            )
                            rec["count"] += 1
                            rec["firstTs"] = rec["firstTs"] or datetime.now().strftime("%H:%M:%S")
                            if len(rec["samples"]) < 3:
                                rec["samples"].append(parsed.get("args"))
                        if kind != "pong":
                            self.log({"phase": "recv", "parsed": parsed})

                    # 心跳必须回 pong，频率与 pingInterval 一致
                    if saw_ping:
                        try:
                            client.post(
                                self._url(sid=self.sid),
                                content=encode_payload("3").encode(),
                                headers={"Content-Type": "text/plain;charset=UTF-8"},
                            )
                            self.request_count += 1
                        except Exception as exc:  # noqa: BLE001
                            self.log({"phase": "pong", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.error = f"线程异常: {exc}"
            self.log({"phase": "fatal", "error": str(exc)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--channels", default="socketio,scrmio")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cookie = load_cookie()
    token = token_from_cookie(cookie)
    print(f"[probe] 通道={args.channels} 时长={args.seconds}s token长度={len(token)}")

    watchers: list[ChannelWatcher] = []
    for name in args.channels.split(","):
        name = name.strip()
        if name not in CHANNELS:
            continue
        w = ChannelWatcher(name, CHANNELS[name], token, args.seconds, OUT_DIR / f"{name}_{ts}.jsonl")
        watchers.append(w)

    for w in watchers:
        w.start()
        time.sleep(1.5)  # 两条通道先后建连，避免同时打

    for w in watchers:
        w.join()

    summary = {
        "startedAt": ts,
        "seconds": args.seconds,
        "channels": {},
    }
    for w in watchers:
        summary["channels"][w.name_] = {
            "sid": w.sid,
            "httpRequests": w.request_count,
            "error": w.error,
            "counts": w.counts,
            "events": w.events,
        }
    (OUT_DIR / f"summary_{ts}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
