"""一知 AICC 接口探测工具（低频、单发、留存原始响应）。

用法:
    python scripts/probe_aicc.py GET  /apiPlatform/user/getUserInfo --name user_info
    python scripts/probe_aicc.py POST /customerDataPlatform/account/pageAccount --name page_account --json '{"pageNum":1,"pageSize":3}'

Cookie 来源: 环境变量 AICC_COOKIE，或 state/probe/cookie.txt（整行原始 Cookie）。
响应原文: state/probe/<name>.json，请求元信息: state/probe/<name>.req.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = "https://aicc.tanyibot.com"
PROBE_DIR = Path("state/probe")
SLEEP_SECONDS = 2.5
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)


def load_cookie() -> str:
    cookie = os.environ.get("AICC_COOKIE", "").strip()
    if cookie:
        return cookie
    f = PROBE_DIR / "cookie.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    raise SystemExit("缺少 Cookie：设置环境变量 AICC_COOKIE 或写入 state/probe/cookie.txt")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("method")
    ap.add_argument("path")
    ap.add_argument("--name", required=True, help="落盘文件名，不含扩展名")
    ap.add_argument("--json", dest="body", default=None, help="JSON 请求体")
    ap.add_argument("--json-file", dest="body_file", default=None, help="JSON 请求体文件（避免命令行引号被吞）")
    ap.add_argument("--form", action="append", default=None, help="表单字段 key=value，可重复")
    ap.add_argument("--max", type=int, default=1800, help="终端输出字符上限")
    args = ap.parse_args()

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    cookie = load_cookie()
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8").strip()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE}/active-scrm-admin/customerManagement",
        "User-Agent": UA,
        "Cookie": cookie,
    }

    kwargs: dict = {"headers": headers, "timeout": 30}
    if body is not None:
        kwargs["content"] = body.encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    elif args.form:
        kwargs["data"] = dict(kv.split("=", 1) for kv in args.form)

    time.sleep(SLEEP_SECONDS)
    with httpx.Client(base_url=BASE, follow_redirects=False) as client:
        resp = client.request(args.method.upper(), args.path, **kwargs)

    text = resp.text
    (PROBE_DIR / f"{args.name}.json").write_text(text, encoding="utf-8")
    (PROBE_DIR / f"{args.name}.req.json").write_text(
        json.dumps(
            {
                "method": args.method.upper(),
                "path": args.path,
                "headers": {k: v for k, v in headers.items() if k != "Cookie"},
                "body": body,
                "form": args.form,
                "httpStatus": resp.status_code,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = {"httpStatus": resp.status_code, "bytes": len(text)}
    try:
        payload = resp.json()
        data = payload.get("data")
        if isinstance(data, dict):
            keys = sorted(data.keys())
            shape = {"keyCount": len(keys), "keySample": keys[:15]}
        elif isinstance(data, list):
            shape = {"listLength": len(data)}
        else:
            shape = type(data).__name__
        summary.update(
            {
                "code": payload.get("code"),
                "resultMsg": payload.get("resultMsg") or payload.get("message"),
                "dataShape": shape,
            }
        )
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        pretty = text

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(pretty[: args.max])
    if len(pretty) > args.max:
        print(f"... [truncated, total {len(pretty)} chars] -> state/probe/{args.name}.json")


if __name__ == "__main__":
    sys.exit(main())
