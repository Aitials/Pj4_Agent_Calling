"""Agent 工具层。

新增工具后在这里登记到 ALL_TOOLS，Agent 初始化时自动注入。
"""

from __future__ import annotations

from app.tools.aicc_dashboard import query_aicc_dashboard
from app.tools.aicc_task_forms import (
    open_create_task_form,
    open_start_task_form,
    query_aicc_task_status,
)
from app.tools.customer_query import query_customer_profile
from app.tools.feishu_message import find_feishu_member, send_feishu_message
from app.tools.feishu_task import create_feishu_task

ALL_TOOLS = [
    query_customer_profile,
    query_aicc_dashboard,
    query_aicc_task_status,
    open_create_task_form,
    open_start_task_form,
    find_feishu_member,
    send_feishu_message,
    create_feishu_task,
]

# 目前只有飞书通道实现了这些工具：其他渠道不暴露，避免模型调用后才发现不可用
# 用元组而非集合：StructuredTool 不可哈希
FEISHU_ONLY = (
    query_aicc_task_status,
    open_create_task_form,
    open_start_task_form,
    find_feishu_member,
    send_feishu_message,
    create_feishu_task,
)


def tools_for(channel: str) -> list:
    """按当前通道返回可用工具；未指定通道时返回全部。"""
    name = (channel or "").strip().lower()
    if not name or name == "feishu":
        return list(ALL_TOOLS)
    return [tool for tool in ALL_TOOLS if tool not in FEISHU_ONLY]

__all__ = [
    "ALL_TOOLS",
    "FEISHU_ONLY",
    "tools_for",
    "create_feishu_task",
    "find_feishu_member",
    "open_create_task_form",
    "open_start_task_form",
    "query_aicc_dashboard",
    "query_aicc_task_status",
    "query_customer_profile",
    "send_feishu_message",
]
