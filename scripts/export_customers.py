"""好客搜客户资料导出（终端打印版）。

复用 app.clients.haokesou 的取数逻辑，只负责命令行参数与打印。

用法：
    .venv\\Scripts\\python.exe scripts\\export_customers.py
    .venv\\Scripts\\python.exe scripts\\export_customers.py --cookie "PHPSESSID=xxx"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.clients.haokesou import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_UA,
    CookieExpiredError,
    collect_customers,
    format_customers,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="好客搜客户资料导出（终端打印）")
    parser.add_argument(
        "--cookie",
        default="",
        help="临时覆盖 Cookie；不传则读环境变量 HAOKESOU_COOKIE 或 state/.haokesou_cookie",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="站点地址")
    parser.add_argument("--limit", type=int, default=50, help="每页条数，默认 50")
    parser.add_argument("--max-pages", type=int, default=200, help="最大翻页数，防止死循环")
    parser.add_argument("--ua", default=DEFAULT_UA, help="自定义 User-Agent")
    args = parser.parse_args()

    # Windows 终端中文输出
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    if args.cookie.strip():
        os.environ["HAOKESOU_COOKIE"] = args.cookie.strip()

    print(f"目标：{args.base_url}")
    print(f"分页：limit={args.limit}，最多 {args.max_pages} 页\n")

    def on_progress(page: int, fetched: int, total: int) -> None:
        print(f"  已拉取第 {page} 页，累计 {fetched}/{total} 条")

    try:
        rows = collect_customers(
            limit=args.limit,
            max_pages=args.max_pages,
            base_url=args.base_url,
            ua=args.ua,
            on_progress=on_progress,
        )
    except CookieExpiredError as exc:
        print(f"[Cookie 失效] {exc}")
        print("请重新登录好客搜后台，更新 state/.haokesou_cookie 后重试。")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[导出失败] {type(exc).__name__}: {exc}")
        return 1

    print(f"\n共 {len(rows)} 条客户记录：\n")
    print(format_customers(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
