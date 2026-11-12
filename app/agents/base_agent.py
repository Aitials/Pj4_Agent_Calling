"""最小 Agent：LangChain create_agent + 工具层。

LLM 用 ChatOpenAI 兼容写法，换模型只改 .env；工具在 app.tools 登记后自动注入。
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.tools import ALL_TOOLS

SYSTEM_PROMPT = (
    "你是一个简洁高效的助手，请用简体中文回答。"
    "用户要查看、导出好客搜客户资料时，调用 export_haokesou_customers 工具获取真实数据；"
    "按手机号或姓名查某个客户的档案时，调用 query_customer_profile 工具。"
    "不要编造客户信息，工具返回的是纯文本，直接转述或复述即可。"
    "如果工具提示 Cookie 失效、客户不存在或数据库未就绪，如实告知用户对应原因。"
)


def _message_text(content) -> str:
    """兼容字符串与分段内容两种返回格式。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return str(content)


class BaseAgent:
    """通用 Agent，持有 LLM 与工具集。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # ChatOpenAI 兼容写法：任何兼容 OpenAI 协议的服务只需换 base_url / model
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
        self.agent = create_agent(
            self.llm,
            tools=ALL_TOOLS,
            system_prompt=SYSTEM_PROMPT,
        )

    def invoke(self, text: str) -> str:
        """同步调用，返回最终文本结果。"""
        result = self.agent.invoke({"messages": [{"role": "user", "content": text}]})
        if isinstance(result, dict) and result.get("messages"):
            return _message_text(result["messages"][-1].content)
        return str(result)

    async def astream(self, text: str):
        """流式产出文本增量，供聊天通道做打字机效果。"""
        async for event in self.agent.astream_events(
            {"messages": [{"role": "user", "content": text}]}, version="v2"
        ):
            if event["event"] != "on_chat_model_stream":
                continue
            content = event["data"]["chunk"].content
            if isinstance(content, str) and content:
                yield content
