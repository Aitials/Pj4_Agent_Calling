"""通道无关的消息处理与启动调度。"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from app.agents.base_agent import BaseAgent
from app.channels.base import Channel, IncomingMessage
from app.core.config import Settings
from app.repositories import memory

log = logging.getLogger("router")

_AGENT: BaseAgent | None = None
_LOCKS: dict[str, asyncio.Lock] = {}


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # httpx 每次请求都会打 INFO，长连接下过于嘈杂
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def init_agent() -> BaseAgent:
    """初始化 Agent：连 PG checkpoint 连接池，全进程复用一个实例。"""
    global _AGENT
    if _AGENT is None:
        checkpointer = await memory.init_memory()
        _AGENT = BaseAgent(Settings.load(), checkpointer=checkpointer)
    return _AGENT


def _lock_for(conversation_id: str) -> asyncio.Lock:
    """同一会话串行处理，避免同一 thread 并发写 checkpoint 互相覆盖。"""
    lock = _LOCKS.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[conversation_id] = lock
    return lock


async def handle_message(channel: Channel, message: IncomingMessage) -> None:
    """把一条入站消息交给 Agent，并交回通道回复。

    短期记忆由 LangGraph checkpointer 负责：
    1. thread_id = 会话标识，历史状态自动从 PG 加载
    2. Agent 只接收本轮 user 消息
    3. 回复完成后无需手工写库
    """
    conv_id = message.conversation_id or message.user_id
    log.info("收到 [%s] conversation=%s user=%s: %s",
             channel.name, conv_id, message.user_id, message.text[:200])

    agent = _AGENT
    if agent is None:
        log.error("Agent 未初始化，丢弃消息 conversation=%s", conv_id)
        return

    async with _lock_for(conv_id):
        try:
            await channel.send_typing(message, True)

            answer = await channel.reply_stream(
                message,
                agent.astream(text=message.text, thread_id=conv_id),
            )
            log.info("回复完成 conversation=%s 长度=%d", conv_id, len(answer or ""))

        except Exception:  # noqa: BLE001
            log.exception("处理消息失败")
            try:
                await channel.reply_text(message, "抱歉，处理失败了，请稍后再试。")
            except Exception:  # noqa: BLE001
                log.exception("发送失败提示也失败")
        finally:
            try:
                await channel.send_typing(message, False)
            except Exception:  # noqa: BLE001
                log.debug("关闭 typing 状态失败", exc_info=True)


def create_channel(name: str) -> Channel:
    if name == "feishu":
        from app.channels.feishu import FeishuChannel

        return FeishuChannel()
    raise SystemExit(f"未知通道：{name}（当前仅支持 feishu）")


async def run_channel(name: str) -> None:
    global _AGENT
    channel = create_channel(name)
    await init_agent()
    log.info("通道已启动：%s", channel.name)
    try:
        await channel.start(partial(handle_message, channel))
    finally:
        await memory.close_memory()
        _AGENT = None
