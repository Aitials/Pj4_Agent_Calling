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

__all__ = [
    "ALL_TOOLS",
    "create_feishu_task",
    "find_feishu_member",
    "open_create_task_form",
    "open_start_task_form",
    "query_aicc_dashboard",
    "query_aicc_task_status",
    "query_customer_profile",
    "send_feishu_message",
]
