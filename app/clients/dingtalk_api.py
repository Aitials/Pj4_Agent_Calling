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
import uuid

import httpx

from app.core.config import (
    DINGTALK_CARD_TEMPLATE_FORM,
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
# AI 互动卡片：流式输出（打字机）
# ---------------------------------------------------------------------------


def _card_request(method: str, path: str, payload: dict) -> bool:
    """卡片接口调用；失败记日志并返回 False，由调用方决定是否降级。"""
    for attempt in range(2):
        token = access_token(force=attempt > 0)
        if not token:
            return False
        try:
            with _http_client() as client:
                response = client.request(
                    method,
                    path,
                    headers={"x-acs-dingtalk-access-token": token},
                    json=payload,
                )
                data = _json(response)
        except Exception:  # noqa: BLE001
            log.exception("钉钉卡片接口请求失败 path=%s", path)
            return False

        if response.status_code == 401 and attempt == 0:
            log.info("钉钉 accessToken 失效，刷新后重试 path=%s", path)
            continue

        code = str(data.get("code") or "")
        if response.status_code >= 400 or code not in ("", "0"):
            log.warning(
                "钉钉卡片接口失败 path=%s %s",
                path,
                _format_error(response.status_code, data),
            )
            return False
        return True
    return False


def stream_card(
    card_instance_id: str,
    text: str,
    *,
    append: bool = False,
    finalize: bool = False,
) -> bool:
    """流式更新 AI 卡片正文；append=False 表示全量替换（默认）。

    finalize=True 用于收尾：钉钉侧只有收到结束标记才会停止打字机，
    并以此段内容作为正文最终形态。
    """
    if not card_instance_id:
        return False
    return _card_request(
        "PUT",
        "/v1.0/card/streaming",
        {
            "outTrackId": card_instance_id,
            "guid": str(uuid.uuid1()),
            "key": "msgContent",
            "content": text,
            "isFull": not append,
            "isFinalize": finalize,
            "isError": False,
        },
    )


def update_card(card_instance_id: str, card_data: dict) -> bool:
    """整体更新卡片数据（收尾、状态切换）。"""
    if not card_instance_id:
        return False
    return _card_request(
        "PUT",
        "/v1.0/card/instances",
        {
            "outTrackId": card_instance_id,
            "cardData": {"cardParamMap": card_data},
        },
    )


# AI Markdown 卡片模板的固定渲染顺序；flowStatus 必须是字符串 "3"
_AI_CARD_ORDER = [
    "msgTitle",
    "msgContent",
    "staticMsgContent",
    "msgTextList",
    "msgImages",
    "msgSlider",
    "msgButtons",
]


def ai_card_text_data(title: str, text: str) -> dict:
    """把 AI Markdown 卡替换成纯文本态（按钮操作后收敛卡片）。"""
    return {
        "msgTitle": title or "任务卡片",
        "msgContent": text or "",
        "staticMsgContent": "",
        "flowStatus": "3",
        "sys_full_json_obj": json.dumps(
            {"order": list(_AI_CARD_ORDER)}, ensure_ascii=False
        ),
    }


def button_card_consumed_response(text: str = "已处理，请查看最新消息。") -> dict:
    """按钮点击后的 ACK 响应：把 AI 卡原地换成无按钮的失效态。

    Stream 模式下 SDK 会把本字典放进 ACK 的 data.response，卡片随 ACK 一起
    更新，不再额外调 update_card —— 既满足 2 秒窗口，又从根上避免「异步
    update 与发新卡并发写同一张卡」导致的客户端重载态。
    """
    card_data = ai_card_text_data("任务卡片", text)
    return {
        "cardUpdateOptions": {"updateCardDataByKey": True},
        "cardData": {"cardParamMap": _stringify_card_data(card_data)},
    }


# ---------------------------------------------------------------------------
# 表单卡：模板卡片 + 表单变量（阶段 4 主路径）
# ---------------------------------------------------------------------------

# 官方文档公开的测试模板；仅开发期使用，生产必须导入自有模板
PUBLIC_FORM_TEMPLATE_ID = "280f6d7a-63bc-4905-bf3f-4c6d95e5166b.schema"
_public_template_warned = False


def _stringify_card_data(data: dict) -> dict:
    """cardParamMap 只接受字符串：非字符串统一 JSON 序列化。"""
    result: dict[str, str] = {}
    for key, value in data.items():
        result[str(key)] = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
    return result


def create_and_deliver_card(
    user_id: str,
    template_id: str,
    card_data: dict,
    callback_type: str = "STREAM",
) -> str:
    """创建并投放模板卡片，返回 outTrackId；失败抛 RuntimeError。"""
    user_id = str(user_id or "").strip()
    template_id = str(template_id or "").strip()
    if not user_id:
        raise RuntimeError("钉钉卡片发送失败：缺少 userId")
    if not template_id:
        raise RuntimeError("钉钉卡片发送失败：缺少卡片模板 ID")

    out_track_id = f"dt4-{uuid.uuid4().hex}"
    _openapi_post(
        "/v1.0/card/instances",
        {
            "cardTemplateId": template_id,
            "outTrackId": out_track_id,
            "cardData": {"cardParamMap": card_data},
            "callbackType": callback_type,
            "imRobotOpenSpaceModel": {"supportForward": False},
        },
    )
    _openapi_post(
        "/v1.0/card/instances/deliver",
        {
            "outTrackId": out_track_id,
            "userIdType": 1,
            "openSpaceId": f"dtv1.card//IM_ROBOT.{user_id}",
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        },
    )
    log.info(
        "钉钉卡片已投放 userId=%s template=%s outTrackId=%s",
        user_id,
        template_id,
        out_track_id,
    )
    return out_track_id


def send_form_card(
    user_id: str,
    title: str,
    fields: list[dict],
    hidden: dict | None = None,
    btn_text: str = "提交",
    template_id: str = "",
) -> str:
    """发送表单卡：字段动态传入，隐藏字段用于携带 token/action。"""
    template = (template_id or DINGTALK_CARD_TEMPLATE_FORM).strip()
    global _public_template_warned
    if template == PUBLIC_FORM_TEMPLATE_ID and not _public_template_warned:
        _public_template_warned = True
        log.warning(
            "正在使用钉钉公开测试表单模板，生产请导入自有模板并配置 "
            "DINGTALK_CARD_TEMPLATE_FORM"
        )

    all_fields = [dict(item) for item in fields or [] if isinstance(item, dict)]
    for key, value in (hidden or {}).items():
        all_fields.append(
            {
                "name": str(key),
                "type": "TEXT",
                "hidden": True,
                "defaultValue": value,
            }
        )

    card_data = _stringify_card_data(
        {
            "title": title or "任务卡片",
            "form_status": "normal",
            "form_btn_text": btn_text or "提交",
            "form": {"fields": all_fields},
        }
    )
    return create_and_deliver_card(user_id, template, card_data)


def form_submitted_response(form: dict, current_form: dict) -> dict:
    """表单提交后的回调响应：回显已填值并把表单置为禁用。

    Stream 模式下 SDK 会把该字典放进 ACK 的 data.response，
    不需要再调用更新接口，天然满足 2 秒窗口。
    """
    form = form or {}
    current = dict(current_form or {})
    fields: list[dict] = []
    for field in current.get("fields") or []:
        if not isinstance(field, dict):
            continue
        item = dict(field)
        name = item.get("name")
        if name in form:
            item["defaultValue"] = form[name]
        fields.append(item)
    current["fields"] = fields

    user_private = {
        "form": current,
        "form_status": "disabled",
        "form_btn_text": "已提交",
    }
    return {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
        "userPrivateData": {"cardParamMap": _stringify_card_data(user_private)},
    }


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


# ---------------------------------------------------------------------------
# 待办：派单与 Agent 建待办共用（需要 unionId，权限 Todo.Todo.Write）
# ---------------------------------------------------------------------------


class TodoError(RuntimeError):
    """待办接口可重试失败（网络/5xx/限流之外的临时异常）。"""


class TodoQuotaExceeded(RuntimeError):
    """当日待办数量达到上限（400002），重试无意义。"""


def _todo_request(method: str, path: str, payload: dict) -> dict:
    """待办接口调用：区分「当天重复内容」与「当日上限」，其余按可重试处理。"""
    last_error = "未知错误"
    for attempt in range(2):
        token = access_token(force=attempt > 0)
        if not token:
            raise TodoError("钉钉 accessToken 获取失败，无法创建待办")
        try:
            with _http_client() as client:
                response = client.request(
                    method,
                    path,
                    headers={"x-acs-dingtalk-access-token": token},
                    json=payload,
                )
                data = _json(response)
        except Exception as exc:  # noqa: BLE001
            raise TodoError(f"钉钉待办接口请求失败：{exc}") from exc

        if response.status_code == 401 and attempt == 0:
            log.info("钉钉 accessToken 失效，刷新后重试 path=%s", path)
            continue

        code = str(data.get("code") or "")
        if response.status_code < 400 and code in ("", "0"):
            return data

        text = _format_error(response.status_code, data)
        last_error = text
        # 官方防骚扰限制：400001 当天已有相同内容待办；400002 当天待办数量上限
        if "400001" in text:
            log.info("钉钉待办当天已存在相同内容，按已创建处理：%s", text)
            return {"_duplicated": True}
        if "400002" in text:
            raise TodoQuotaExceeded(f"钉钉待办今日已达上限：{text}")
        raise TodoError(f"钉钉待办接口失败：{text}")
    raise TodoError(f"钉钉待办接口失败：{last_error}")


def create_todo(
    union_id: str,
    subject: str,
    *,
    description: str = "",
    source_id: str = "",
    due_ms: int | None = None,
) -> dict:
    """创建钉钉待办，返回 {"id", "url", "duplicated"}。

    sourceId 用于业务幂等；同一通电话重试时内容与 sourceId 保持一致，
    即使前一次创建成功但没来得及落库，也会命中 400001 按已创建处理。
    """
    union_id = str(union_id or "").strip()
    subject = str(subject or "").strip()
    if not union_id:
        raise ValueError("创建钉钉待办缺少 unionId")
    if not subject:
        raise ValueError("创建钉钉待办缺少标题")

    payload: dict = {
        "subject": subject[:1024],
        "description": str(description or "")[:4096],
        "executorIds": [union_id],
        "participantIds": [],
    }
    if source_id:
        payload["sourceId"] = str(source_id)[:64]
    if due_ms:
        payload["dueTime"] = int(due_ms)

    data = _todo_request(
        "POST", f"/v1.0/todo/users/{union_id}/tasks", payload
    )
    if data.get("_duplicated"):
        return {"id": "", "url": "", "duplicated": True}
    return {
        "id": str(data.get("id") or ""),
        "url": str(data.get("detailUrl") or data.get("url") or ""),
        "duplicated": False,
    }


