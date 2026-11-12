"""最小 Agent：只打通 LangChain -> ChatOpenAI 调用链路，暂不含业务提示词。

后续接入业务能力时，在 app/tools 下补充工具，并在本类初始化时通过 bind_tools 注入。
"""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.core.config import Settings


class BaseAgent:
    """通用对话 Agent。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # ChatOpenAI 兼容写法：任何兼容 OpenAI 协议的服务只需换 base_url / model
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "你是一个乐于助人的助手，请用简体中文简洁回答。"),
                ("human", "{input}"),
            ]
        )
        self.chain = self.prompt | self.llm | StrOutputParser()

    def invoke(self, text: str) -> str:
        """同步调用，返回纯文本结果。"""
        return self.chain.invoke({"input": text})

    async def astream(self, text: str):
        """流式产出文本增量，供聊天通道做打字机效果。"""
        async for chunk in self.chain.astream({"input": text}):
            if isinstance(chunk, str) and chunk:
                yield chunk
