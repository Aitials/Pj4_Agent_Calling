"""好客搜客户端：自动登录 + Cookie 缓存。

登录流程：
  1. 新建 httpx.Client → GET /user/index/verify 拿验证码图片
  2. ddddocr 识别（最多重试 8 次）
  3. POST /user/index/login 带 username/password/verify
  4. code=1 取 PHPSESSID，存为 JSON 文件：{"cookie": "...", "expires_at": <unix 时间戳>}

Cookie 缓存策略：
  - 默认有效期 2 小时（COOKIE_TTL_SEC）
  - ensure_cookie() 每次先读 state/.haokesou_cookie，未过期直接返回，不重登
  - 过期或文件不存在 → OCR 登录刷新一次
  - 环境变量 HAOKESOU_COOKIE 永远优先（忽略过期），用于手动覆盖

调用方（haokesou.py）额外做兜底：接口返回 Cookie 失效时强制 refresh 一次。
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Optional

import ddddocr
import httpx

from app.core.config import PROJECT_ROOT

COOKIE_FILE = PROJECT_ROOT / "state" / ".haokesou_cookie"
LOGIN_URL = "http://whxt.jshks.com/user/index/login"
VERIFY_URL = "http://whxt.jshks.com/user/index/verify"
LOGIN_SUCCESS_CODE = 1  # 好客搜登录成功码是 1

# Cookie 缓存有效期（秒），登录时刻写入 expires_at = now + TTL
# 实测好客搜 PHPSESSID 大约 2 小时过期，保守用 7200 秒
COOKIE_TTL_SEC = 7200

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "http://whxt.jshks.com/user/index/login",
}


class LoginError(RuntimeError):
    """登录失败：验证码识别不了、账号密码错误、或接口异常。"""


# ---------------------------------------------------------------------------
# Cookie 存储（JSON 文件：{cookie: str, expires_at: int}）
# ---------------------------------------------------------------------------


def _save(cookie: str, expires_at: int) -> None:
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(
        json.dumps({"cookie": cookie, "expires_at": expires_at}, ensure_ascii=False),
        encoding="utf-8",
    )


def _load() -> Optional[tuple[str, int]]:
    """返回 (cookie, expires_at) 或 None。"""
    if not COOKIE_FILE.exists():
        return None
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        cookie = data.get("cookie", "").strip()
        expires_at = int(data.get("expires_at", 0))
        if not cookie or "把这里替换" in cookie or expires_at <= 0:
            return None
        return cookie, expires_at
    except (json.JSONDecodeError, ValueError, TypeError):
        # 兼容老版本：文件里只有纯 Cookie 字符串
        text = COOKIE_FILE.read_text(encoding="utf-8").strip()
        if text and "把这里替换" not in text:
            # 老文件：把 expires_at 视作 0 → 立刻重登（保守）
            return text, 0
        return None


# ---------------------------------------------------------------------------
# 直接登录
# ---------------------------------------------------------------------------


def _ocr_captcha(client: httpx.Client, ocr) -> str:
    """拉取验证码图片并识别，返回 4 位字符串。"""
    resp = client.get(VERIFY_URL, headers={"Referer": LOGIN_URL})
    resp.raise_for_status()
    result = ocr.classification(resp.content)
    return (result or "").strip()


def _do_login(username: str, password: str, max_attempts: int = 8) -> str:
    """完整登录流程：新 client → OCR 验证码 → POST 登录 → 返回 Cookie 字符串。"""
    ocr = ddddocr.DdddOcr(show_ad=False)
    client = httpx.Client(headers=DEFAULT_HEADERS, timeout=15.0)

    last_error: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        try:
            verify = _ocr_captcha(client, ocr)
        except Exception as exc:  # noqa: BLE001
            last_error = f"验证码下载失败：{exc}"
            continue

        if len(verify) < 3 or len(verify) > 6:
            time.sleep(random.uniform(0.3, 0.6))
            continue

        try:
            resp = client.post(
                LOGIN_URL,
                data={"username": username, "password": password, "verify": verify},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            last_error = f"登录请求异常：{exc}"
            time.sleep(random.uniform(0.3, 0.6))
            continue

        if data.get("code") == LOGIN_SUCCESS_CODE:
            sess = client.cookies.get("PHPSESSID")
            if not sess:
                raise LoginError(f"登录成功但没拿到 PHPSESSID，响应：{data}")
            return f"PHPSESSID={sess}"

        msg = data.get("msg") or data.get("message") or "登录失败"
        if "验证码" in msg:
            last_error = f"验证码错误（第 {attempt} 次，识别 {verify!r}）"
            time.sleep(random.uniform(0.4, 0.8))
            continue

        client.close()
        raise LoginError(f"登录失败：{msg}")

    client.close()
    raise LoginError(f"验证码识别多次失败（共尝试 {max_attempts} 次）。最后错误：{last_error}")


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------


def refresh_cookie(username: str, password: str) -> str:
    """强制登录并覆写缓存（调用方需要兜底时用这个）。"""
    cookie = _do_login(username, password)
    expires_at = int(time.time()) + COOKIE_TTL_SEC
    _save(cookie, expires_at)
    return cookie


def get_stored_cookie() -> Optional[str]:
    """只读：返回缓存里的 Cookie（不管是否过期），或环境变量覆盖。"""
    env_val = os.environ.get("HAOKESOU_COOKIE", "").strip()
    if env_val and "把这里替换" not in env_val:
        return env_val
    cached = _load()
    if cached:
        return cached[0]
    return None


def ensure_cookie(force: bool = False) -> str:
    """返回一份可用的 Cookie 字符串。

    规则：
      1. 环境变量 HAOKESOU_COOKIE 永远优先返回（忽略过期，手动覆盖场景）
      2. force=True → 强制重登
      3. 缓存文件存在且 expires_at > now → 直接返回
      4. 否则走 OCR 登录刷新
    """
    username = os.environ.get("HAOKESOU_USERNAME", "").strip()
    password = os.environ.get("HAOKESOU_PASSWORD", "").strip()

    # 1. 环境变量覆盖
    env_val = os.environ.get("HAOKESOU_COOKIE", "").strip()
    if env_val and "把这里替换" not in env_val:
        return env_val

    # 2. 缓存文件未过期
    if not force:
        cached = _load()
        if cached:
            cookie, expires_at = cached
            if expires_at > time.time():
                return cookie

    # 3. 走登录
    if username and password:
        return refresh_cookie(username, password)

    # 4. 没账号密码也没有效缓存
    raise LoginError(
        "未配置好客搜账号密码（.env 里缺 HAOKESOU_USERNAME / HAOKESOU_PASSWORD），"
        "也没有可用的 state/.haokesou_cookie，请至少提供其中一项。"
    )
