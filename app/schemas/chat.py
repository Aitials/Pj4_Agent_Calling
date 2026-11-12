"""对话请求/响应模型，后续接 API 层可直接复用。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentRequest(BaseModel):
    input: str = Field(..., description="用户输入文本")


class AgentResponse(BaseModel):
    output: str = Field(..., description="模型输出文本")
