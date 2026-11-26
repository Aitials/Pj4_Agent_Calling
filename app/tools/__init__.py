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
from app.tools.dingtalk_todo import create_dingtalk_todo
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

# 飞书专属：其他渠道一律不暴露
FEISHU_ONLY = (
    find_feishu_member,
    send_feishu_message,
    create_feishu_task,
)

# 钉钉：通用查询 + 创建 + 启动 + 状态 + 待办（4.6 全放行，阶段 6 加待办）
DINGTALK_TOOLS = (
    query_customer_profile,
    query_aicc_dashboard,
    query_aicc_task_status,
    open_create_task_form,
    open_start_task_form,
    create_dingtalk_todo,
)

# 企微暂未接入卡片流程，只保留通用查询
WECOM_TOOLS = (
    query_customer_profile,
    query_aicc_dashboard,
)


def tools_for(channel: str) -> list:
    """按当前通道返回可用工具；未指定通道时返回全部。"""
    name = (channel or "").strip().lower()
    if not name or name == "feishu":
        return list(ALL_TOOLS)
    if name == "dingtalk":
        return list(DINGTALK_TOOLS)
    return list(WECOM_TOOLS)

__all__ = [
    "ALL_TOOLS",
    "FEISHU_ONLY",
    "DINGTALK_TOOLS",
    "WECOM_TOOLS",
    "tools_for",
    "create_dingtalk_todo",
    "create_feishu_task",
    "find_feishu_member",
    "open_create_task_form",
    "open_start_task_form",
    "query_aicc_dashboard",
    "query_aicc_task_status",
    "query_customer_profile",
    "send_feishu_message",
]
