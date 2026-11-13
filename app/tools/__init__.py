"""Agent 工具层。

新增工具后在这里登记到 ALL_TOOLS，Agent 初始化时自动注入。
"""

from __future__ import annotations

from app.tools.customer_query import query_customer_profile
from app.tools.feishu_message import find_feishu_member, send_feishu_message
from app.tools.feishu_task import create_feishu_task
from app.tools.haokesou_customers import export_haokesou_customers

ALL_TOOLS = [
    export_haokesou_customers,
    query_customer_profile,
    find_feishu_member,
    send_feishu_message,
    create_feishu_task,
]

__all__ = [
    "ALL_TOOLS",
    "create_feishu_task",
    "export_haokesou_customers",
    "find_feishu_member",
    "query_customer_profile",
    "send_feishu_message",
]
