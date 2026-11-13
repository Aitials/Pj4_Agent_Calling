"""飞书开放平台「出站」调用封装：主动发消息 + 创建任务。

与 app/channels/feishu.py 的分工：
  - channels/feishu.py：长连接收消息，在会话上下文里回复（依赖 IncomingMessage）
  - clients/feishu_api.py：无上下文依赖的主动推送与任务派发

凭证复用 .env 里的 FEISHU_APP_ID / FEISHU_APP_SECRET，
lark.Client 内部自动获取并缓存 tenant_access_token，无需手动刷新。
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_lib
from typing import Any, Iterable

import lark_oapi as lark
from lark_oapi.api.contact.v3 import (
    BatchGetIdUserRequest,
    BatchGetIdUserRequestBody,
    ListUserRequest,
)
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.task.v2 import CreateTaskRequest
from lark_oapi.api.task.v2.model import Due, InputTask, Member, Start

from app.core.config import FEISHU_APP_ID, FEISHU_APP_SECRET

log = logging.getLogger("feishu_api")

# 一次批量换 open_id 的上限（飞书限制 50）
BATCH_RESOLVE_LIMIT = 50

_ERROR_HINTS = {
    230006: "应用未开启「机器人」能力：开发者后台 → 添加应用能力 → 机器人 → 重新发布版本",
    230013: "目标用户不在应用可用范围内：开发者后台 → 版本管理 → 可用范围 → 添加该用户后重新发版",
    230002: "机器人不在该群：先把应用机器人拉进目标群",
    230017: "机器人只能发送自己上传的文件",
    230027: "缺少权限：请申请 im:message:send_as_bot 或 task:task:write 后重新发版",
    230020: "触发频率限制（单聊 5 QPS），请降低发送频率",
}

_client: lark.Client | None = None


def _get_client() -> lark.Client:
    global _client
    if _client is None:
        if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
            raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先在 .env 中配置")
        _client = (
            lark.Client.builder()
            .app_id(FEISHU_APP_ID)
            .app_secret(FEISHU_APP_SECRET)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
    return _client


def _ensure_ok(response: Any, action: str) -> None:
    if response.success():
        return
    hint = _ERROR_HINTS.get(response.code)
    detail = f"{action}失败：code={response.code} msg={response.msg}"
    if hint:
        detail = f"{detail}；{hint}"
    raise RuntimeError(detail)


# ---------------------------------------------------------------- 人员解析


def resolve_open_ids(
    mobiles: Iterable[str] | None = None,
    emails: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """按手机号 / 邮箱批量换 open_id。

    返回形如 [{"open_id": "ou_xxx", "mobile": "138...", "email": ""}]。
    查不到的人不会出现在结果里（未加入企业或已离职）。

    需要权限：contact:user.id:readonly（或 contact:user.employee_id:readonly）
    """
    mobile_list = [m.strip() for m in (mobiles or []) if m and m.strip()]
    email_list = [e.strip() for e in (emails or []) if e and e.strip()]
    if not mobile_list and not email_list:
        raise ValueError("mobiles 与 emails 不能同时为空")
    if len(mobile_list) + len(email_list) > BATCH_RESOLVE_LIMIT:
        raise ValueError(f"单次最多 {BATCH_RESOLVE_LIMIT} 个号码/邮箱")

    body = (
        BatchGetIdUserRequestBody.builder()
        .mobiles(mobile_list)
        .emails(email_list)
        .include_resigned(False)
        .build()
    )
    request = (
        BatchGetIdUserRequest.builder()
        .user_id_type("open_id")
        .request_body(body)
        .build()
    )
    response = _get_client().contact.v3.user.batch_get_id(request)
    _ensure_ok(response, "手机号/邮箱换 open_id")

    result: list[dict[str, str]] = []
    for info in (response.data.user_list if response.data else None) or []:
        if not info.user_id:
            continue
        result.append(
            {
                "open_id": info.user_id,
                "mobile": info.mobile or "",
                "email": info.email or "",
            }
        )
    return result


def resolve_open_id_by_mobile(mobile: str) -> str | None:
    """单个手机号换 open_id，查不到返回 None。"""
    found = resolve_open_ids(mobiles=[mobile])
    return found[0]["open_id"] if found else None


def list_all_users(page_size: int = 50) -> list[dict]:
    """拉取应用通讯录可见范围内的全部成员，自动翻页。

    注意：列表接口不返回 department_ids，部门需按 open_id 单独查询。
    """
    page_size = max(1, min(int(page_size or 50), 50))
    users: list[dict] = []
    page_token = ""

    while True:
        builder = (
            ListUserRequest.builder()
            .user_id_type("open_id")
            .department_id_type("open_department_id")
            .page_size(page_size)
        )
        if page_token:
            builder.page_token(page_token)

        response = _get_client().contact.v3.user.list(builder.build())
        _ensure_ok(response, "拉取通讯录成员")

        data = response.data
        if not data:
            break

        for user in data.items or []:
            users.append(
                {
                    "open_id": user.open_id or "",
                    "union_id": user.union_id or "",
                    "name": user.name or "",
                    "en_name": user.en_name or "",
                    "mobile_visible": bool(user.mobile_visible),
                    "is_agent": bool(getattr(user, "is_agent", False)),
                    "raw": {
                        key: value
                        for key, value in vars(user).items()
                        if value is not None
                        and isinstance(value, (str, int, float, bool))
                    },
                }
            )

        page_token = data.page_token or ""
        if not data.has_more or not page_token:
            break

    return users


# ---------------------------------------------------------------- 发消息


def send_message(
    receive_id: str,
    msg_type: str,
    content: dict,
    *,
    receive_id_type: str = "open_id",
    dedup_key: str | None = None,
) -> dict:
    """主动发消息的底层方法。

    receive_id_type 可选：open_id / user_id / union_id / email / chat_id
    dedup_key 传入后同一 key 1 小时内只会成功发送一次（飞书幂等）。
    """
    if not receive_id:
        raise ValueError("receive_id 不能为空")

    body = (
        CreateMessageRequestBody.builder()
        .receive_id(receive_id)
        .msg_type(msg_type)
        .content(json.dumps(content, ensure_ascii=False))
        .uuid(dedup_key or str(uuid_lib.uuid4()))
        .build()
    )
    request = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(body)
        .build()
    )
    response = _get_client().im.v1.message.create(request)
    _ensure_ok(response, "主动发送消息")
    return {"message_id": getattr(response.data, "message_id", "")}


def send_text(
    receive_id: str,
    text: str,
    *,
    receive_id_type: str = "open_id",
    dedup_key: str | None = None,
) -> dict:
    """给指定用户（或群）发一条纯文本。"""
    return send_message(
        receive_id,
        "text",
        {"text": text},
        receive_id_type=receive_id_type,
        dedup_key=dedup_key,
    )


def send_card(
    receive_id: str,
    card: dict,
    *,
    receive_id_type: str = "open_id",
    dedup_key: str | None = None,
) -> dict:
    """给指定用户（或群）发一张交互卡片（JSON 2.0 结构）。"""
    return send_message(
        receive_id,
        "interactive",
        card,
        receive_id_type=receive_id_type,
        dedup_key=dedup_key,
    )


def build_task_card(summary: str, task_guid: str, description: str = "") -> dict:
    """构造一张「任务已派发」的通知卡片，配合 send_card 使用。

    用 markdown 超链接代替 action 按钮，避免 schema 2.0 兼容性问题。
    """
    task_url = f"https://applink.feishu.cn/client/todo/detail?guid={task_guid}"
    content_lines = [f"**{summary}**"]
    if description:
        content_lines.append(description)
    content_lines.append(f"[👉 点击查看任务]({task_url})")

    return {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "新任务已派发"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(content_lines)},
            ]
        },
    }


# ---------------------------------------------------------------- 建任务


def create_task(
    summary: str,
    assignee_open_ids: Iterable[str],
    *,
    follower_open_ids: Iterable[str] = (),
    description: str = "",
    due_ms: int | None = None,
    start_ms: int | None = None,
    client_token: str | None = None,
) -> str:
    """创建一个飞书任务并指派负责人，返回任务 guid。

    summary            任务标题（必填，≤3000 字符）
    assignee_open_ids  负责人 open_id 列表（至少一个）
    follower_open_ids  关注人 open_id 列表
    due_ms / start_ms  毫秒时间戳
    client_token       幂等 token（10~100 字符），重复调用不会重复建任务

    需要权限：task:task:write
    """
    assignees = [i for i in assignee_open_ids if i]
    if not summary.strip():
        raise ValueError("summary 不能为空")
    if not assignees:
        raise ValueError("assignee_open_ids 至少要有一个负责人")

    members: list[Member] = [
        Member.builder().id(open_id).type("user").role("assignee").build()
        for open_id in assignees
    ]
    members.extend(
        Member.builder().id(open_id).type("user").role("follower").build()
        for open_id in follower_open_ids
        if open_id
    )

    builder = (
        InputTask.builder()
        .summary(summary.strip())
        .description(description)
        .members(members)
    )
    if due_ms:
        builder.due(Due.builder().timestamp(int(due_ms)).is_all_day(False).build())
    if start_ms:
        builder.start(Start.builder().timestamp(int(start_ms)).is_all_day(False).build())
    if client_token:
        builder.client_token(client_token)

    request = (
        CreateTaskRequest.builder()
        .user_id_type("open_id")
        .request_body(builder.build())
        .build()
    )
    response = _get_client().task.v2.task.create(request)
    _ensure_ok(response, "创建任务")

    guid = getattr(response.data.task, "guid", "") if response.data else ""
    if not guid:
        raise RuntimeError("创建任务成功但响应缺少 task.guid")
    log.info("飞书任务已创建 guid=%s summary=%s", guid, summary)
    return guid


# ---------------------------------------------------------------- 组合场景


def assign_task_with_notice(
    mobile: str,
    summary: str,
    *,
    description: str = "",
    due_ms: int | None = None,
) -> dict:
    """按手机号找人 → 建任务 → 发卡片通知，返回 {open_id, task_guid}。

    这是「派单给坐席」最常用的一条链路。
    """
    open_id = resolve_open_id_by_mobile(mobile)
    if not open_id:
        raise RuntimeError(f"手机号 {mobile} 在飞书通讯录里查不到 open_id")

    guid = create_task(
        summary,
        [open_id],
        description=description,
        due_ms=due_ms,
    )
    send_card(open_id, build_task_card(summary, guid, description))
    return {"open_id": open_id, "task_guid": guid}
