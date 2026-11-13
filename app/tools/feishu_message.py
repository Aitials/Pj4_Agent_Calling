"""飞书消息工具：本地查人 + 主动发消息。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.clients.feishu_api import send_text
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


@tool
def find_feishu_member(name: str = "", department: str = "") -> str:
    """查询飞书通讯录成员，返回姓名、部门和 open_id。

    Args:
        name: 姓名关键词，支持部分匹配，如「张三」或「张」。
        department: 部门名称关键词，用于缩小范围。当前部门信息尚未同步，传了会被忽略。

    命中一个返回详情，命中多个返回列表，未命中给出明确提示。
    """
    name = (name or "").strip()
    department = (department or "").strip()

    if not name and not department:
        return "请提供姓名或部门关键词。"

    if not name:
        return "当前通讯录尚未同步部门信息，无法按部门查询。请提供姓名关键词。"

    try:
        rows = search_members(name)
    except ValueError as exc:
        return str(exc)
    except DatabaseError as exc:
        return f"查询失败：{exc}"

    if not rows:
        return f"未找到姓名为「{name}」的成员。"

    note = ""
    if department:
        note = "\n\n提示：部门信息尚未同步，部门条件未生效。"

    if len(rows) == 1:
        row = rows[0]
        return (
            f"姓名：{row.get('name') or '-'}\n"
            f"部门：{row.get('department_names') or '未同步'}\n"
            f"open_id：{row.get('open_id') or '-'}{note}"
        )

    return _render_members(rows, f"找到 {len(rows)} 个匹配「{name}」的成员，请确认是哪一个：") + note


@tool
def send_feishu_message(receiver: str, content: str) -> str:
    """向飞书成员发送一条文本消息。

    Args:
        receiver: 收件人。可以是姓名（如「张三」），也可以是 open_id（如「ou_xxx」）。
        content: 消息正文，纯文本。

    姓名不唯一时不会发送，会返回候选列表，需补充更精确的姓名或改用 open_id。
    """
    receiver = (receiver or "").strip()
    content = (content or "").strip()

    if not receiver:
        return "请提供收件人。"
    if not content:
        return "消息内容不能为空。"

    try:
        if receiver.startswith("ou_"):
            member = get_member(receiver)
            open_id = receiver
            label = f"{member.get('name')}（{open_id}）" if member else open_id
        else:
            rows = search_members(receiver)
            if not rows:
                return f"未找到姓名为「{receiver}」的成员。可先用 find_feishu_member 查询确认。"
            if len(rows) > 1:
                return _render_members(
                    rows, f"「{receiver}」命中 {len(rows)} 个成员，为避免发错人，请指定 open_id："
                )
            open_id = rows[0]["open_id"]
            label = f"{rows[0].get('name')}（{open_id}）"
    except DatabaseError as exc:
        return f"查询收件人失败：{exc}"

    try:
        result = send_text(open_id, content)
    except Exception as exc:  # 飞书接口或网络异常
        return f"发送失败：{exc}"

    return f"✅ 已发送给 {label}\nmessage_id：{result.get('message_id')}"
