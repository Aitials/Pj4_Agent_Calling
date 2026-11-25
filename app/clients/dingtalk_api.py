"""钉钉企业内部应用 HTTP 能力（Stream 收消息之外的主动调用）。

accessToken 有效期 7200 秒，必须缓存并提前刷新；
所有 OpenAPI 调用都受「服务端出口 IP 白名单」约束，
未填写或与实际出口不一致会直接返回权限/白名单错误。
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx

from app.core.config import (
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
    DINGTALK_ROBOT_CODE,
)

log = logging.getLogger("dingtalk_api")

API_BASE = "https://api.dingtalk.com"
HTTP_TIMEOUT_SECONDS = 5.0
TOKEN_SAFETY_SECONDS = 300.0
# 单聊批量发送每次最多 20 人
SINGLE_CHAT_BATCH = 20

_token: dict[str, float | str] = {"value": "", "expires_at": 0.0}
_token_lock = threading.Lock()


def is_configured() -> bool:
    """是否已配置 Stream 与 OpenAPI 共用的应用凭证。"""
    return bool(DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET)


def _http_client() -> httpx.Client:
    """企业内网代理不一定放行平台域名，直连更稳（与企微一致）。"""
    return httpx.Client(
        base_url=API_BASE, timeout=HTTP_TIMEOUT_SECONDS, trust_env=False
    )


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _format_error(status: int, data: dict) -> str:
    """把钉钉的错误体转成可读文本，带上缺失权限点与 requestId 便于排查。"""
    parts = [f"http={status}"]
    if data.get("code"):
        parts.append(f"code={data['code']}")
    if data.get("message"):
        parts.append(f"message={data['message']}")
    detail = data.get("AccessDeniedDetail")
    if isinstance(detail, dict):
        scopes = detail.get("requiredScopes") or detail.get("requiredScopesList")
        if scopes:
            parts.append(f"requiredScopes={scopes}")
    if data.get("requestid"):
        parts.append(f"requestid={data['requestid']}")
    return " ".join(parts)


def access_token(force: bool = False) -> str:
    """获取企业内部应用 accessToken（带缓存）；失败返回空串，不抛异常。"""
    now = time.monotonic()
    with _token_lock:
        if not force and _token["value"] and now < float(_token["expires_at"]):
            return str(_token["value"])
        if not is_configured():
            return ""

        try:
            with _http_client() as client:
                response = client.post(
                    "/v1.0/oauth2/accessToken",
                    json={
                        "appKey": DINGTALK_CLIENT_ID,
                        "appSecret": DINGTALK_CLIENT_SECRET,
                    },
                )
                data = _json(response)
        except Exception:  # noqa: BLE001
            log.exception("获取钉钉 accessToken 失败")
            return ""

        if response.status_code >= 400 or not data.get("accessToken"):
            log.error(
                "获取 accessToken 被拒绝：%s", _format_error(response.status_code, data)
            )
            return ""

        expire_in = float(data.get("expireIn") or 7200)
        _token["value"] = str(data["accessToken"])
        _token["expires_at"] = now + max(expire_in - TOKEN_SAFETY_SECONDS, 60.0)
        log.info("钉钉 accessToken 已刷新，有效期 %.0f 秒", expire_in)
        return str(_token["value"])


def _openapi_request(
    method: str, path: str, params: dict | None = None, payload: dict | None = None
) -> dict:
    """带 token 重试的 OpenAPI 调用；失败抛 RuntimeError，便于冒烟脚本展示原因。"""
    last_error = "未知错误"
    for attempt in range(2):
        token = access_token(force=attempt > 0)
        if not token:
            raise RuntimeError(
                "钉钉 accessToken 获取失败：请检查 Client ID / Secret "
                "以及服务端出口 IP 白名单"
            )
        with _http_client() as client:
            response = client.request(
                method,
                path,
                headers={"x-acs-dingtalk-access-token": token},
                params=params,
                json=payload,
            )
        data = _json(response)

        if response.status_code == 401 and attempt == 0:
            log.info("钉钉 accessToken 失效，刷新后重试 path=%s", path)
            continue

        last_error = _format_error(response.status_code, data)
        code = str(data.get("code") or "")
        if response.status_code >= 400 or code not in ("", "0"):
            raise RuntimeError(f"钉钉接口调用失败：{path} {last_error}")
        return data
    raise RuntimeError(f"钉钉接口调用失败：{path} {last_error}")


def _openapi_post(path: str, payload: dict) -> dict:
    return _openapi_request("POST", path, payload=payload)


def _msg_body(text: str, markdown: bool) -> tuple[str, str]:
    """拼 msgKey / msgParam：msgParam 必须是 JSON 字符串。"""
    if markdown:
        return (
            "sampleMarkdown",
            json.dumps(
                {"title": "AI 助手", "text": text or "（空消息）"},
                ensure_ascii=False,
            ),
        )
    return "sampleText", json.dumps(
        {"content": text or "（空消息）"}, ensure_ascii=False
    )


def send_bot_message(
    user_ids: list[str],
    text: str,
    robot_code: str = "",
    markdown: bool = False,
) -> None:
    """机器人批量发单聊消息（每次最多 20 人）。"""
    robot = (robot_code or DINGTALK_ROBOT_CODE).strip()
    if not robot:
        raise RuntimeError("缺少 DINGTALK_ROBOT_CODE，无法主动发送钉钉单聊消息")

    ids = [str(item).strip() for item in user_ids or [] if str(item).strip()]
    if not ids:
        raise RuntimeError("钉钉单聊发送失败：缺少 userIds")

    msg_key, msg_param = _msg_body(text, markdown)
    for start in range(0, len(ids), SINGLE_CHAT_BATCH):
        chunk = ids[start:start + SINGLE_CHAT_BATCH]
        _openapi_post(
            "/v1.0/robot/oToMessages/batchSend",
            {
                "robotCode": robot,
                "userIds": chunk,
                "msgKey": msg_key,
                "msgParam": msg_param,
            },
        )
        log.info("钉钉单聊消息已发送 userIds=%s", chunk)


def send_group_message(
    open_conversation_id: str, text: str, robot_code: str = "", markdown: bool = False
) -> None:
    """机器人发群聊消息；机器人需先被拉进该群。"""
    robot = (robot_code or DINGTALK_ROBOT_CODE).strip()
    if not robot:
        raise RuntimeError("缺少 DINGTALK_ROBOT_CODE，无法发送钉钉群聊消息")
    if not open_conversation_id:
        raise RuntimeError("钉钉群聊发送失败：缺少 openConversationId")

    msg_key, msg_param = _msg_body(text, markdown)
    _openapi_post(
        "/v1.0/robot/groupMessages/send",
        {
            "robotCode": robot,
            "openConversationId": open_conversation_id,
            "msgKey": msg_key,
            "msgParam": msg_param,
        },
    )
    log.info("钉钉群聊消息已发送 conversation=%s", open_conversation_id)


def mask_secret(value: str, keep: int = 4) -> str:
    """日志/脚本展示凭证时只保留首尾少量字符。"""
    text = str(value or "")
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}…{text[-keep:]}"


# ---------------------------------------------------------------------------
# 身份：userId → unionId（待办接口要 unionId，不参与会话记忆）
# ---------------------------------------------------------------------------

# 转换成功缓存 12 小时；权限/网络异常只缓存 10 分钟，补齐后能自动恢复
UNION_ID_CACHE_TTL = 12 * 3600.0
UNION_ID_FAIL_TTL = 600.0
_UNION_CACHE_MAX_ENTRIES = 2000

_union_cache: dict[str, tuple[str, float]] = {}
_union_lock = threading.Lock()


def _fetch_union_id(user_id: str) -> str | None:
    """返回 unionId；空串表示平台明确查无此人；None 表示网络/权限异常。"""
    try:
        data = _openapi_request(
            "GET",
            "/v1.0/contact/users/batch/get",
            params={"userIdList": json.dumps([user_id], ensure_ascii=False)},
        )
    except Exception as exc:  # noqa: BLE001
        # 典型原因：未开通 qyapi_get_member（成员信息读权限）
        log.warning("钉钉 unionId 查询失败（userid=%s）：%s", user_id, exc)
        return None

    for row in data.get("userList") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("userid") or "") == user_id:
            return str(row.get("unionid") or "")

    unauthorized = data.get("unauthorizedUserIdList") or []
    if user_id in unauthorized:
        log.warning(
            "钉钉用户不在通讯录授权范围内 userId=%s（检查授权范围）", user_id
        )
    return ""


def _cache_union(user_id: str, union: str, ttl: float) -> None:
    now = time.monotonic()
    with _union_lock:
        if len(_union_cache) >= _UNION_CACHE_MAX_ENTRIES:
            for key in [
                key for key, (_, expires_at) in _union_cache.items()
                if expires_at <= now
            ]:
                _union_cache.pop(key, None)
        _union_cache[user_id] = (union, now + ttl)


def union_id(user_id: str) -> str:
    """把明文 userId 换成 unionId；失败返回空串，不抛异常、不阻塞对话。

    仅供阶段 6 创建钉钉待办使用；派单失败时由调用方降级提示。
    """
    user_id = str(user_id or "").strip()
    if not user_id:
        return ""

    now = time.monotonic()
    with _union_lock:
        hit = _union_cache.get(user_id)
        if hit and now < hit[1]:
            return hit[0]

    fetched = _fetch_union_id(user_id)
    if not fetched:
        _cache_union(user_id, "", UNION_ID_FAIL_TTL)
        return ""

    _cache_union(user_id, fetched, UNION_ID_CACHE_TTL)
    log.info("钉钉 unionId 转换成功 userId=%s", user_id)
    return fetched


