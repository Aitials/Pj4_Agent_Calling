"""飞书任务工具：本地查人 → 创建任务 → 发卡片通知。"""

from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool

from app.clients.feishu_api import build_task_card, create_task, send_card
from app.core.textfmt import display_width, pad
from app.repositories.customer_repo import DatabaseError
from app.repositories.feishu_user_repo import get_member, search_members

MEMBER_COLUMNS = [
    ("序号", 4),
    ("姓名", 12),
    ("部门", 14),
    ("open_id", 38),
]


def _render_members(rows: list[dict], title: str) -> str:
    header = " | ".join(pad(name, width) for name, width in MEMBER_COLUMNS)
    lines = [title, "", header, "-" * display_width(header)]
    for index, row in enumerate(rows, start=1):
        values = [
            str(index),
            row.get("name") or "-",
            row.get("department_names") or "未同步",
            row.get("open_id") or "-",
        ]
        lines.append(
            " | ".join(pad(value, width) for value, (_, width) in zip(values, MEMBER_COLUMNS))
        )
    return "\n".join(lines)


def _parse_due(due_str: str | None) -> int | None:
    """解析截止时间字符串为毫秒时间戳。支持 "YYYY-MM-DD HH:MM" 或 "YYYY-MM-DD"。"""
    if not due_str:
        return None

    text = due_str.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


@tool(parse_docstring=True)
def create_feishu_task(
    summary: str,
    assignee: str,
    description: str = "",
    due: str = "",
) -> str:
    """创建飞书任务并派发给负责人，同时发送卡片通知（真实写入）。

    何时使用：用户明确要求给某个人建任务/待办，并说明了要做什么。
    不要使用：只是发消息（用 send_feishu_message）、只是提醒、或负责人不明确；
              重名时先确认，不要猜人。
    副作用：会在飞书创建真实任务，并向负责人发送卡片通知；
            卡片失败不影响任务创建，会提示手动通知。
    返回：任务 GUID 与查看链接；重名时不创建任务，返回候选列表。

    Args:
        summary: 任务标题，必填。简明描述要做什么。
        assignee: 负责人，必填。姓名（如「张三」）或 open_id（如「ou_xxx」）。
        description: 任务详情/要求/备注，可选。
        due: 截止时间，可选。格式「YYYY-MM-DD HH:MM」或「YYYY-MM-DD」；不传表示无截止时间。
    """
    summary = (summary or "").strip()
    assignee = (assignee or "").strip()
    description = (description or "").strip()
    due_str = (due or "").strip()

    if not summary:
        return "任务标题不能为空。"
    if not assignee:
        return "请指定负责人（姓名或 open_id）。"

    # 1) 定位负责人 open_id
    try:
        if assignee.startswith("ou_"):
            member = get_member(assignee)
            open_id = assignee
            name_label = member.get("name") if member else assignee
        else:
            rows = search_members(assignee)
            if not rows:
                return f"未找到姓名为「{assignee}」的成员。可先用 find_feishu_member 查询确认。"
            if len(rows) > 1:
                return _render_members(
                    rows, f"「{assignee}」命中 {len(rows)} 个成员，为避免派错人，请指定 open_id："
                )
            open_id = rows[0]["open_id"]
            name_label = rows[0].get("name") or assignee
    except DatabaseError as exc:
        return f"查询负责人失败：{exc}"

    # 2) 解析截止时间
    due_ms = _parse_due(due_str)
    if due_str and due_ms is None:
        return (
            f"无法解析截止时间「{due_str}」。"
            "请使用 YYYY-MM-DD HH:MM 格式，如「2026-11-14 18:00」。"
        )

    # 3) 创建任务
    try:
        guid = create_task(
            summary,
            [open_id],
            description=description,
            due_ms=due_ms,
        )
    except Exception as exc:
        return f"创建任务失败：{exc}"

    # 4) 发送卡片通知（失败不阻塞，任务已建）
    card = build_task_card(summary, guid, description)
    try:
        send_card(open_id, card)
        card_info = "✅ 卡片通知已发送"
    except Exception as exc:
        card_info = f"⚠️ 卡片通知发送失败（任务已创建，请手动通知）：{exc}"

    due_display = (
        datetime.fromtimestamp(due_ms / 1000).strftime("%Y-%m-%d %H:%M")
        if due_ms
        else "无"
    )
    return (
        f"✅ 任务已创建并派发给 {name_label}\n"
        f"任务标题：{summary}\n"
        f"截止时间：{due_display}\n"
        f"任务 GUID：{guid}\n"
        f"查看链接：https://applink.feishu.cn/client/todo/detail?guid={guid}\n"
        f"{card_info}"
    )
