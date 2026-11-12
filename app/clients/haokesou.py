"""好客搜客户端层。

只封装已验证的接口：客户列表（含意向标签）。
Cookie 支持三种来源：环境变量 HAOKESOU_COOKIE > state/.haokesou_cookie 文件。
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime
from typing import Callable

import httpx

from app.clients.haokesou_auth import ensure_cookie, refresh_cookie
from app.core.config import PROJECT_ROOT
from app.core.textfmt import display_width as _width, pad as _pad

API_PATH = "/user/Member/ajax_intentional_member"
REFERER = "http://whxt.jshks.com/user/member/intentional_member"
DEFAULT_BASE_URL = "http://whxt.jshks.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

# 客户意愿分类
CRM_CATE = {
    0: "未分类",
    1: "意向客户",
    2: "沟通中",
    3: "试用中",
    4: "已成交",
    5: "无意向",
    6: "再跟踪",
    7: "未接",
}
# 意向等级：1~6 对应 A~F
LEVEL = {1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F"}

COLUMNS = [
    ("序号", 4),
    ("手机号", 15),
    ("姓名", 12),
    ("客户意愿", 8),
    ("意向等级", 8),
    ("拨打次数", 8),
    ("所属任务", 14),
    ("创建时间", 19),
]


class CookieExpiredError(RuntimeError):
    """接口返回非 JSON（通常是登录页 / 错误页），判定为 Cookie 失效。"""


def _fmt_time(value) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "-"


def _build_headers(cookie: str, ua: str = DEFAULT_UA) -> dict[str, str]:
    return {
        "Cookie": cookie,
        "User-Agent": ua,
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
    }


def _build_form(page: int, limit: int) -> dict[str, str]:
    """按前端 JS 的参数结构构造表单，数组为空表示不限。"""
    return {
        "page": str(page),
        "limit": str(limit),
        "phone": "",
        "name": "",
        "sitchair": "",
        "config_id": "",
        "startCreateDate": "",
        "endCreateDate": "",
        "min_call_count": "",
        "max_call_count": "",
    }


def fetch_customer_page(client: httpx.Client, page: int, limit: int, retries: int = 2) -> dict:
    """拉取一页客户数据，网络异常重试；非 JSON 响应判定 Cookie 失效。"""
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.post(API_PATH, data=_build_form(page, limit))
            resp.raise_for_status()
            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                snippet = resp.text[:200].replace("\n", " ")
                raise CookieExpiredError(
                    f"接口返回的不是 JSON（可能 Cookie 失效）：{snippet}"
                ) from exc
        except CookieExpiredError:
            raise
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 + attempt)
    raise RuntimeError(f"请求第 {page} 页失败：{last_error}")


def collect_customers(
    limit: int = 50,
    max_pages: int = 200,
    base_url: str = DEFAULT_BASE_URL,
    ua: str = DEFAULT_UA,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[dict]:
    """全量翻页拉取客户列表，Cookie 失效时强制刷新重试一次。"""
    username = os.environ.get("HAOKESOU_USERNAME", "").strip()
    password = os.environ.get("HAOKESOU_PASSWORD", "").strip()

    def _try_once(force_refresh: bool = False) -> list[dict]:
        cookie = ensure_cookie(force=force_refresh)
        headers = _build_headers(cookie, ua)

        rows: list[dict] = []
        total = 0
        page = 1

        with httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=20.0,
            follow_redirects=False,
        ) as client:
            while page <= max_pages:
                payload = fetch_customer_page(client, page, limit)
                if payload.get("code") != 1:
                    raise RuntimeError(
                        f"接口业务失败：code={payload.get('code')} msg={payload.get('msg')}"
                    )

                data = payload.get("data") or {}
                batch = data.get("list") or []
                if total == 0:
                    total = int(data.get("total") or 0)
                if not batch:
                    break

                rows.extend(batch)
                if on_progress:
                    on_progress(page, len(rows), total)

                if total and len(rows) >= total:
                    break
                page += 1
                time.sleep(random.uniform(0.3, 0.8))

        return rows

    try:
        return _try_once(force_refresh=False)
    except CookieExpiredError:
        # Cookie 真的失效了，强制重新登录一次再试
        if username and password:
            refresh_cookie(username, password)
            return _try_once(force_refresh=True)
        raise


def format_customers(rows: list[dict]) -> str:
    """把客户列表渲染成等宽文本表格，供终端打印或工具返回。"""
    if not rows:
        return "（无数据）"

    header = " | ".join(_pad(name, width) for name, width in COLUMNS)
    lines = [header, "-" * _width(header)]

    for index, row in enumerate(rows, start=1):
        cate = CRM_CATE.get(row.get("crm_cate"), str(row.get("crm_cate")))
        level = LEVEL.get(row.get("phone_level"), "-")
        values = [
            str(index),
            str(row.get("phone") or "-"),
            str(row.get("name") or "-"),
            cate,
            level,
            str(row.get("call_times") or 0),
            str(row.get("task_name") or "-"),
            _fmt_time(row.get("create_time")),
        ]
        lines.append(
            " | ".join(_pad(value, width) for value, (_, width) in zip(values, COLUMNS))
        )

    return "\n".join(lines)
