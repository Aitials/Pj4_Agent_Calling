"""企业微信智能机器人客户端工厂与自建应用 HTTP 能力。

长连接模式只需 Bot ID + Secret，不涉及公网回调地址与消息加解密。
自建应用凭证（CorpID + 应用 Secret）用于换取 access_token，
把回调里的加密 open_userid 转成明文 userid，供会话记忆按工号隔离。
"""

from __future__ import annotations

import logging
import threading
import time

import httpx
from aibot import WSClient, WSClientOptions

from app.core.config import (
    WECHAT_AGENT_ID,
    WECHAT_AGENT_SECRET,
    WECHAT_BOT_ID,
    WECHAT_BOT_SECRET,
    WECHAT_CORP_ID,
)

log = logging.getLogger("wecom_api")


class _SdkLogger:
    """把 SDK 日志接到项目统一的 logging 上。"""

    def debug(self, message: str, *args: object) -> None:
        log.debug(message, *args)

    def info(self, message: str, *args: object) -> None:
        log.info(message, *args)

    def warn(self, message: str, *args: object) -> None:
        log.warning(message, *args)

    def error(self, message: str, *args: object) -> None:
        log.error(message, *args)


def is_configured() -> bool:
    """是否已配置长连接所需凭证。"""
    return bool(WECHAT_BOT_ID and WECHAT_BOT_SECRET)


def build_ws_client() -> WSClient:
    """创建企微长连接客户端；缺凭证直接报错，避免静默失败。"""
    if not is_configured():
        raise RuntimeError("缺少 WECHAT_BOT_ID / WECHAT_BOT_SECRET，请在 .env 中配置")
    return WSClient(
        WSClientOptions(
            bot_id=WECHAT_BOT_ID,
            secret=WECHAT_BOT_SECRET,
            logger=_SdkLogger(),
            # 长连接常驻，断线无限重连
            max_reconnect_attempts=-1,
        )
    )


# ---------------------------------------------------------------------------
# 自建应用：access_token 与 open_userid → userid
# ---------------------------------------------------------------------------

API_BASE = "https://qyapi.weixin.qq.com"
HTTP_TIMEOUT_SECONDS = 3.0
TOKEN_SAFETY_SECONDS = 300.0
# 转换成功或判定为明文后的缓存时长
USERID_CACHE_TTL = 12 * 3600.0
# 网络异常 / 权限不足只缓存 10 分钟，凭证或权限补齐后能自动恢复
USERID_FAIL_TTL = 600.0
_CACHE_MAX_ENTRIES = 2000

_token: dict[str, float | str] = {"value": "", "expires_at": 0.0}
_token_lock = threading.Lock()
_userid_cache: dict[str, tuple[str, float]] = {}
_userid_lock = threading.Lock()


def _http_client() -> httpx.Client:
    """企业内网代理不一定放行平台域名，直连更稳（与 tanyibot 保持一致）。"""
    return httpx.Client(
        base_url=API_BASE, timeout=HTTP_TIMEOUT_SECONDS, trust_env=False
    )


def is_identity_configured() -> bool:
    """是否已配置可换取 access_token 的自建应用凭证。"""
    return bool(WECHAT_CORP_ID and WECHAT_AGENT_SECRET)


def access_token(force: bool = False) -> str:
    """获取 access_token（带缓存）；失败返回空串，不抛异常。"""
    now = time.monotonic()
    with _token_lock:
        if not force and _token["value"] and now < float(_token["expires_at"]):
            return str(_token["value"])
        if not is_identity_configured():
            return ""

        try:
            with _http_client() as client:
                response = client.get(
                    "/cgi-bin/gettoken",
                    params={
                        "corpid": WECHAT_CORP_ID,
                        "corpsecret": WECHAT_AGENT_SECRET,
                    },
                )
                data = response.json()
        except Exception:  # noqa: BLE001
            log.exception("获取企业微信 access_token 失败")
            return ""

        if not isinstance(data, dict) or data.get("errcode") not in (0, None):
            log.error(
                "获取 access_token 被拒绝：errcode=%s errmsg=%s",
                data.get("errcode") if isinstance(data, dict) else "-",
                data.get("errmsg") if isinstance(data, dict) else "-",
            )
            return ""
        if not data.get("access_token"):
            log.error("access_token 响应缺少字段：%s", str(data)[:200])
            return ""

        expires_in = float(data.get("expires_in") or 7200)
        _token["value"] = str(data["access_token"])
        _token["expires_at"] = now + max(expires_in - TOKEN_SAFETY_SECONDS, 60.0)
        log.info("access_token 已刷新，有效期 %.0f 秒", expires_in)
        return str(_token["value"])


def openuserid_to_userid(open_userids: list[str]) -> dict[str, str] | None:
    """把加密 open_userid 转成明文 userid。

    返回 {open_userid: userid}；平台明确答复但无映射时返回空 dict
    （说明该 ID 本身已是明文）；网络异常或权限不足返回 None。
    """
    ids = [str(item).strip() for item in open_userids or [] if str(item).strip()]
    if not ids:
        return {}

    payload: dict[str, object] = {"open_userid_list": ids}
    if WECHAT_AGENT_ID.isdigit():
        payload["source_agentid"] = int(WECHAT_AGENT_ID)

    for attempt in range(2):
        # 第二次强制刷新 token，规避 token 过期导致的整轮失败
        token = access_token(force=attempt > 0)
        if not token:
            return None
        try:
            with _http_client() as client:
                response = client.post(
                    "/cgi-bin/batch/openuserid_to_userid",
                    params={"access_token": token},
                    json=payload,
                )
                data = response.json()
        except Exception:  # noqa: BLE001
            log.exception("open_userid 转换请求失败")
            return None
        if not isinstance(data, dict):
            log.warning("open_userid 转换返回非预期格式：%s", str(data)[:200])
            return None

        errcode = data.get("errcode")
        if errcode in (40014, 42001) and attempt == 0:
            log.info("access_token 已失效（errcode=%s），刷新后重试", errcode)
            continue
        if errcode not in (0, None):
            log.warning(
                "open_userid 转换失败：errcode=%s errmsg=%s（将按原始 ID 兜底）",
                errcode,
                data.get("errmsg"),
            )
            return None

        mapping: dict[str, str] = {}
        for row in data.get("userid_list") or []:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("open_userid") or "").strip()
            plain = str(row.get("userid") or "").strip()
            if raw and plain:
                mapping[raw] = plain
        return mapping
    return None


def _cache_userid(raw: str, plain: str, ttl: float) -> None:
    now = time.monotonic()
    with _userid_lock:
        if len(_userid_cache) >= _CACHE_MAX_ENTRIES:
            for key in [
                key for key, (_, expires_at) in _userid_cache.items()
                if expires_at <= now
            ]:
                _userid_cache.pop(key, None)
        _userid_cache[raw] = (plain, now + ttl)


def resolve_userid(raw: str) -> str:
    """把回调里的发送人 ID 解析成会话标识；永远返回可用字符串，不抛异常。

    未配置自建应用凭证、无权限、网络异常时一律退回原始 ID，
    保证消息与记忆始终可用，只是 thread_id 可能不是工号。
    """
    raw = str(raw or "").strip()
    if not raw:
        return ""

    now = time.monotonic()
    with _userid_lock:
        hit = _userid_cache.get(raw)
        if hit and now < hit[1]:
            return hit[0]

    if not is_identity_configured():
        _cache_userid(raw, raw, USERID_FAIL_TTL)
        return raw

    mapping = openuserid_to_userid([raw])
    if mapping is None:
        _cache_userid(raw, raw, USERID_FAIL_TTL)
        return raw

    plain = mapping.get(raw) or ""
    if plain:
        _cache_userid(raw, plain, USERID_CACHE_TTL)
        if plain != raw:
            log.info("企微身份转换成功 raw=%s plain=%s", raw, plain)
        return plain

    # 平台没有给出映射：该 ID 本身就是明文 userid（例如超管创建的机器人）
    _cache_userid(raw, raw, USERID_CACHE_TTL)
    log.debug("open_userid 无映射，按明文 userid 使用 user=%s", raw)
    return raw


def mask_secret(value: str, keep: int = 4) -> str:
    """日志/脚本展示凭证时只保留首尾少量字符。"""
    text = str(value or "")
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}…{text[-keep:]}"
