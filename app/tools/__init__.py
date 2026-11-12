"""Agent 工具层。

新增工具后在这里登记到 ALL_TOOLS，Agent 初始化时自动注入。
"""

from __future__ import annotations

from app.tools.customer_query import query_customer_profile
from app.tools.haokesou_customers import export_haokesou_customers

ALL_TOOLS = [export_haokesou_customers, query_customer_profile]

__all__ = ["ALL_TOOLS", "export_haokesou_customers", "query_customer_profile"]
