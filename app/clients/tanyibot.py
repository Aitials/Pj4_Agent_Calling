"""一知 AICC 客户端：Cookie 自动登录续期 + 统一请求封装。

鉴权事实（2026-11-17 实测）：
- 业务接口只认 Cookie，没有自定义认证头；Cookie 需包含 token / refresh_token / SERVERID；
- 没有 refresh 接口，refresh_token 无法换票，只能重新走登录接口；
- 登录接口 `POST /apiPlatform/entry/login`（表单），密码用 AES-192-ECB + PKCS7 加密后 base64，
  密钥是前端硬编码字符串，按 UTF-8 取 24 字节（不能 base64 解码后再当 key）。
"""

from __future__ import annotations

import base64
import logging
import threading
from typing import Any

import httpx
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from app.core.config import PROJECT_ROOT, get_env

log = logging.getLogger("aicc")

BASE_URL = get_env("AICC_BASE_URL", "https://aicc.tanyibot.com").strip().rstrip("/")
_ENV_PATH = PROJECT_ROOT / ".env"
# CryptoJS 里是 enc.Utf8.parse(key)，因此 key 是这串字符的 UTF-8 字节，共 24 字节 → AES-192
_AES_KEY = b"tiihtNczf5v6AKRyjwEUhQ=="
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)

_cookie: str = get_env("AICC_COOKIE", "").strip()

# 平台对批量并发敏感：全局限制同时在途的请求数（登录重试不占用递归名额）
AICC_MAX_CONCURRENCY = max(
    1, int(get_env("AICC_MAX_CONCURRENCY", "2").strip() or 2)
)
_call_slots = threading.BoundedSemaphore(AICC_MAX_CONCURRENCY)


class AiccError(RuntimeError):
    """平台返回的业务错误（HTTP 200 + code != 200）。"""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def encrypt_password(raw: str) -> str:
    cipher = AES.new(_AES_KEY, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pad(raw.encode("utf-8"), 16))).decode()


def _skip_proxy() -> dict[str, Any]:
    """企业内网代理不一定放行平台域名，直连更稳。"""
    return {"trust_env": False}


def _persist_cookie(cookie: str) -> None:
    """把新 Cookie 写回 .env 的 AICC_COOKIE 行，供下次进程直接复用。"""
    line = f"AICC_COOKIE={cookie}"
    try:
        content = _ENV_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("未找到 .env，跳过 Cookie 持久化")
        return

    lines = content.splitlines()
    for idx, old in enumerate(lines):
        if old.startswith("AICC_COOKIE="):
            lines[idx] = line
            break
    else:
        lines.append(line)

    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def login() -> str:
    """用账号密码换取新 Cookie，成功后写回 .env 并更新进程内缓存。"""
    global _cookie
    phone = get_env("AICC_PHONE", "").strip()
    password = get_env("AICC_PASSWORD", "").strip()
    if not phone or not password:
        raise AiccError(
            None,
            "Cookie 已失效，且未配置 AICC_PHONE / AICC_PASSWORD，无法自动重新登录。"
            "请在 .env 里补齐账号密码，或从浏览器重新复制 Cookie 到 AICC_COOKIE。",
        )

    with _call_slots:
        with httpx.Client(base_url=BASE_URL, timeout=30, follow_redirects=False, **_skip_proxy()) as client:
            resp = client.post(
                "/apiPlatform/entry/login",
                data={"phoneNumber": phone, "password": encrypt_password(password)},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json, text/plain, */*",
                    "User-Agent": UA,
                },
            )
            client.cookies  # 触发 cookie 收集

    try:
        payload = resp.json()
    except ValueError as exc:
        raise AiccError(None, f"登录响应不是 JSON：{resp.text[:200]}") from exc

    if payload.get("code") != 200:
        raise AiccError(payload.get("code"), payload.get("resultMsg") or "登录失败")

    jars = resp.history[-1].cookies if resp.history else resp.cookies
    cookie = "; ".join(f"{k}={v}" for k, v in jars.items())
    if "token=" not in cookie:
        # 登录成功但拿不到 token 时，保留原 Cookie，让后续请求暴露真实错误
        raise AiccError(None, f"登录成功但未拿到 token Cookie：{cookie[:120]}")

    _cookie = cookie
    _persist_cookie(cookie)
    log.info("一知登录成功，Cookie 已更新并写回 .env")
    return cookie


def current_cookie(refresh: bool = False) -> str:
    global _cookie
    if refresh or not _cookie:
        _cookie = get_env("AICC_COOKIE", "").strip()
    if not _cookie:
        return login()
    return _cookie


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    retry_login: bool = True,
) -> Any:
    """调用平台接口并解包 `data`。

    遇到「未登录」会自动重新登录并重试一次，其余业务错误抛 AiccError。
    """
    cookie = current_cookie()
    headers = {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": UA,
        "Cookie": cookie,
    }
    with _call_slots:
        with httpx.Client(base_url=BASE_URL, timeout=30, follow_redirects=False, **_skip_proxy()) as client:
            resp = client.request(method.upper(), path, params=params, json=json_body, headers=headers)

    try:
        payload = resp.json()
    except ValueError as exc:
        raise AiccError(None, f"响应不是 JSON（HTTP {resp.status_code}）：{resp.text[:200]}") from exc

    code = payload.get("code")
    message = payload.get("resultMsg") or payload.get("message") or ""

    if code == 200:
        return payload.get("data")

    if retry_login and code == 400 and "未登录" in message:
        log.info("Cookie 已失效，尝试自动重新登录")
        login()
        return request(method, path, params=params, json_body=json_body, retry_login=False)

    raise AiccError(code, message or f"接口返回异常（HTTP {resp.status_code}）")


def get(path: str, params: dict[str, Any] | None = None) -> Any:
    return request("GET", path, params=params)


def post(
    path: str,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    """POST 请求；部分接口（如 execute）参数走 query 而非 body。"""
    return request("POST", path, params=params, json_body=json_body)
