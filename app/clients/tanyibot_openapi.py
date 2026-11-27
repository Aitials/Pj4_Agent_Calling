"""一知开放平台（openapi）客户端：appkey + timestamp + signature 鉴权。

与 `tanyibot.py`（Cookie 登录的后台接口）完全独立：
- 域名走 openapi.tanyibot.com；
- 请求头为 appkey / timestamp / signature，不携带 Cookie；
- 失败不回退 Cookie 通道。

签名公式（官方文档「开发引导 / 权限认证」）：
    sha256(tenantSign + "appKey=..&appSecret=..&tenantSign=..&version=v1&timestamp=..")
文档 Python 样例里的 `&amp;` 是 HTML 转义，实际拼接用 `&`；timestamp 为毫秒。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import httpx

from app.core.config import get_env

log = logging.getLogger("aicc_openapi")

BASE_URL = get_env("AICC_OPENAPI_BASE_URL", "https://openapi.tanyibot.com").strip().rstrip("/")
APP_KEY = get_env("AICC_APP_KEY").strip()
APP_SECRET = get_env("AICC_APP_SECRET").strip()
TENANT_SIGN = get_env("AICC_TENANT_SIGN").strip()
VERSION = get_env("AICC_OPENAPI_VERSION", "v1").strip() or "v1"

# 平台对并发敏感：全局限制同时在途的请求数，避免批量刷新压垮网关
MAX_CONCURRENCY = max(1, int(get_env("AICC_OPENAPI_MAX_CONCURRENCY", "3").strip() or 3))
_call_slots = threading.BoundedSemaphore(MAX_CONCURRENCY)


class OpenApiError(RuntimeError):
    """开放平台返回的业务错误（HTTP 200 + code != 200）。"""

    def __init__(self, code: int | None, message: str, request_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def credentials_ready() -> bool:
    """三件套齐全才允许调用；缺失时由业务层转成可读提示。"""
    return bool(APP_KEY and APP_SECRET and TENANT_SIGN)


def signature(timestamp: int) -> str:
    raw = (
        TENANT_SIGN
        + "appKey=" + APP_KEY
        + "&appSecret=" + APP_SECRET
        + "&tenantSign=" + TENANT_SIGN
        + "&version=" + VERSION
        + "&timestamp=" + str(timestamp)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    """调用开放平台接口并解包 `data`；业务错误抛 OpenApiError。"""
    if not credentials_ready():
        raise OpenApiError(
            None,
            "未配置一知开放平台凭证（AICC_APP_KEY / AICC_APP_SECRET / AICC_TENANT_SIGN），请联系管理员",
        )

    timestamp = int(round(time.time() * 1000))
    headers = {
        "appkey": APP_KEY,
        "timestamp": str(timestamp),
        "signature": signature(timestamp),
        "Content-Type": "application/json;charset=utf-8",
        "Accept": "application/json",
    }
    # 企业内网代理不一定放行平台域名，直连更稳（与 Cookie 客户端保持一致）
    with _call_slots:
        with httpx.Client(
            base_url=BASE_URL, timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            resp = client.request(method.upper(), path, params=params, json=json_body, headers=headers)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise OpenApiError(None, f"响应不是 JSON（HTTP {resp.status_code}）：{resp.text[:200]}") from exc

    code = payload.get("code")
    if code == 200:
        return payload.get("data")

    message = payload.get("resultMsg") or payload.get("message") or ""
    raise OpenApiError(code, message or f"接口返回异常（HTTP {resp.status_code}）", payload.get("requestId") or "")


def get(path: str, params: dict[str, Any] | None = None) -> Any:
    return request("GET", path, params=params)


def post(
    path: str,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """POST 请求；部分接口参数走 query（如 magichub 系），统一由调用方指定。"""
    return request("POST", path, params=params, json_body=json_body)
