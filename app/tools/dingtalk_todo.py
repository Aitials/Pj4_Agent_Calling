"""钉钉待办工具：按销售名单定位 userId → 换 unionId → 创建待办。

本期只支持 sales_assignee 名单内的销售，不依赖通讯录全量搜索权限。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from langchain_core.tools import tool

from app.clients import dingtalk_api
from app.repositories import dispatch_repo
from app.repositories.customer_repo import DatabaseError


def _parse_due(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _resolve_assignee(assignee: str) -> tuple[str, str, str]:
    """返回 (userId, 展示名, 错误提示)；错误提示非空表示解析失败。"""
    text = (assignee or "").strip()
    if not text:
        return "", "", "请指定负责人（销售姓名或钉钉 userId）。"
    if text.isdigit():
        return text, text, ""
    try:
        rows = dispatch_repo.find_sales_by_name(text)
    except DatabaseError as exc:
        return "", "", f"查询销售名单失败：{exc}"
    if not rows:
        return "", "", (
            f"销售名单里没有找到「{text}」。"
            "请确认姓名，或让管理员先维护 sales_assignee.dingtalk_userid。"
        )
    if len(rows) > 1:
        candidates = "\n".join(
            f"- {row.get('name') or '-'}（userId {row.get('user_id')}）"
            for row in rows[:10]
        )
        return "", "", f"「{text}」命中多个销售，请改用 userId 指定：\n{candidates}"
    row = rows[0]
    return str(row.get("user_id") or ""), str(row.get("name") or text), ""


@tool(parse_docstring=True)
def create_dingtalk_todo(
    summary: str,
    assignee: str,
    description: str = "",
    due: str = "",
) -> str:
    """创建钉钉待办并派发给销售（真实写入）。

    何时使用：用户明确要求给某个销售建待办/任务，并说明了要做什么。
    不要使用：只是发消息、只是提醒、负责人不明确；重名时先让用户确认，不要猜人。
    副作用：会在钉钉创建真实待办并指派给该销售；同一内容当天可能被钉钉去重。
    返回：待办 ID 与链接；找不到人或重名时返回候选列表。

    Args:
        summary: 待办标题，必填。
        assignee: 负责人，销售姓名或钉钉明文 userId。
        description: 待办详情，可选。
        due: 截止时间，可选；格式「YYYY-MM-DD HH:MM」或「YYYY-MM-DD」。
    """
    summary = (summary or "").strip()
    if not summary:
        return "待办标题不能为空。"

    due_ms = _parse_due(due)
    if due and due_ms is None:
        return "无法解析截止时间，请使用「YYYY-MM-DD HH:MM」或「YYYY-MM-DD」。"

    user_id, name_label, error = _resolve_assignee(assignee)
    if error:
        return error

    union = dingtalk_api.union_id(user_id)
    if not union:
        return (
            f"无法获取销售 {name_label} 的钉钉 unionId。"
            "请确认该用户在通讯录授权范围内，且已开通 qyapi_get_member。"
        )

    try:
        result = dingtalk_api.create_todo(
            union,
            summary,
            description=(description or "").strip(),
            source_id=f"agent-{uuid.uuid4().hex[:20]}",
            due_ms=due_ms,
        )
    except dingtalk_api.TodoQuotaExceeded as exc:
        return f"待办创建失败：{exc}"
    except (dingtalk_api.TodoError, ValueError) as exc:
        return f"待办创建失败：{exc}"

    if result.get("duplicated"):
        return f"钉钉提示今天已有相同内容的待办（{name_label}），未重复创建。"

    todo_id = str(result.get("id") or "")
    url = str(result.get("url") or "")
    lines = [
        f"✅ 待办已创建并派发给 {name_label}",
        f"标题：{summary}",
        f"待办 ID：{todo_id or '-'}",
    ]
    if url:
        lines.append(f"查看链接：{url}")
    return "\n".join(lines)
