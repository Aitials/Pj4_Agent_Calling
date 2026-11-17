"""最小 Agent：LangChain create_agent + 工具层。

LLM 用 ChatOpenAI 兼容写法，换模型只改 .env；工具在 app.tools 登记后自动注入。
短期记忆由 checkpoint（PostgreSQL）负责：每轮只传本轮 user 消息，历史由图状态加载。
"""

from __future__ import annotations

import logging

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AnyMessage, HumanMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.core.config import Settings, get_env
from app.tools import ALL_TOOLS

log = logging.getLogger("agent")

SYSTEM_PROMPT = (
    "你是一个简洁高效的助手，请用简体中文回答。"
    "按手机号或姓名查某个客户的档案时，调用 query_customer_profile 工具。"
    "问外呼情况、看板数据、接通率、在跑任务时，调用 query_aicc_dashboard 工具。"
    "问某个具体任务的运行状态、进度、意向分布时，调用 query_aicc_task_status；"
    "用户提到任务名或任务 ID，就把它作为 task_keyword 传入；没提到具体任务就留空，"
    "工具会先发筛选卡片。不要自己编造任务 ID、状态或进度数据。"
    "只有用户明确说“创建/新建外呼任务”时才调用 open_create_task_form；"
    "只有用户明确说“启动/开始跑某个任务”时才调用 open_start_task_form。"
    "数字、寒暄、追问、上下文不完整等含糊输入，一律不要调用这些工具，先用一句话反问确认。"
    "严格以用户当前这句话的意图为准，不要根据历史对话揣测。"
    "任务的参数由用户在卡片里填写，你只需要提示用户去填写/确认，"
    "不要声称任务已经创建或启动，也不要自己编造参数。"
    "不要编造客户信息，工具返回的是纯文本，直接转述或复述即可。"
    "如果工具提示客户不存在或数据库未就绪，如实告知用户对应原因。"
)

# 上下文裁剪：只把最近 N 轮送给模型，再用 token 上限兜底，防止单轮超长
HISTORY_MAX_ROUNDS = max(1, int(get_env("HISTORY_MAX_ROUNDS", "10").strip() or 10))
HISTORY_MAX_TOKENS = max(500, int(get_env("HISTORY_MAX_TOKENS", "6000").strip() or 6000))


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


def _slice_recent_rounds(messages: list[AnyMessage], max_rounds: int) -> list[AnyMessage]:
    """从后往前保留最近 max_rounds 个用户轮次。

    必须以 HumanMessage 为边界，否则会把 AIMessage(tool_calls) 与
    ToolMessage 拆开，模型会报工具调用未配对。
    """
    human_positions = [
        idx for idx, msg in enumerate(messages) if isinstance(msg, HumanMessage)
    ]
    if len(human_positions) <= max_rounds:
        return messages
    return messages[human_positions[-max_rounds]:]


def trim_history_messages(messages: list[AnyMessage]) -> list[AnyMessage]:
    """先按轮数切，再用 token 上限兜底。"""
    recent = _slice_recent_rounds(messages, HISTORY_MAX_ROUNDS)
    trimmed = trim_messages(
        recent,
        max_tokens=HISTORY_MAX_TOKENS,
        token_counter=count_tokens_approximately,
        strategy="last",
        allow_partial=False,
        include_system=True,
        start_on="human",
    )
    if not trimmed:
        # 单条消息就超过 token 上限时，至少保留最后一条用户消息，避免空上下文
        for msg in reversed(recent):
            if isinstance(msg, HumanMessage):
                return [msg]
        return recent[-1:]
    return trimmed


class _TrimHistoryMiddleware(AgentMiddleware):
    """只裁剪送给模型的消息，checkpoint 里仍保留完整对话。

    同步与异步两套钩子都要实现：飞书通道走 awrap（astream_events），
    命令行 main.py 走 wrap（invoke）。
    """

    @staticmethod
    def _prepare(request: ModelRequest) -> ModelRequest:
        original = list(request.messages)
        trimmed = trim_history_messages(original)
        if len(trimmed) != len(original):
            log.info(
                "上下文裁剪：%d → %d 条（上限 %d 轮 / %d tokens）",
                len(original), len(trimmed), HISTORY_MAX_ROUNDS, HISTORY_MAX_TOKENS,
            )
        return request.override(messages=trimmed)

    def wrap_model_call(self, request: ModelRequest, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest, handler):
        return await handler(self._prepare(request))


def _run_config(thread_id: str | None) -> dict | None:
    if not thread_id:
        return None
    return {"configurable": {"thread_id": thread_id}}


class BaseAgent:
    """通用 Agent，持有 LLM、工具集与（可选的）checkpointer。"""

    def __init__(
        self,
        settings: Settings,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
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
            middleware=[_TrimHistoryMiddleware()],
            checkpointer=checkpointer,
        )

    def invoke(self, text: str, thread_id: str | None = None) -> str:
        """同步调用，返回最终文本结果。

        传 thread_id 时使用 checkpoint 中的历史；不传则为无记忆单轮。
        """
        result = self.agent.invoke(
            {"messages": [{"role": "user", "content": text}]},
            config=_run_config(thread_id),
        )
        if isinstance(result, dict) and result.get("messages"):
            return _message_text(result["messages"][-1].content)
        return str(result)

    async def astream(self, text: str, thread_id: str | None = None):
        """流式产出文本增量，供聊天通道做打字机效果。"""
        async for event in self.agent.astream_events(
            {"messages": [{"role": "user", "content": text}]},
            config=_run_config(thread_id),
            version="v2",
        ):
            if event["event"] != "on_chat_model_stream":
                continue
            content = event["data"]["chunk"].content
            if isinstance(content, str) and content:
                yield content
