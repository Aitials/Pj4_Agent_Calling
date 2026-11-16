"""通道无关的消息处理与启动调度。"""

from __future__ import annotations

import logging
from functools import lru_cache, partial

from app.agents.base_agent import BaseAgent
from app.channels.base import Channel, IncomingMessage
from app.core.config import Settings
from app.repositories import chat_history_repo as hist

log = logging.getLogger("router")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    # httpx 每次请求都会打 INFO，长连接下过于嘈杂
    logging.getLogger("httpx").setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def get_agent() -> BaseAgent:
    """全进程复用一个 Agent 实例。"""
    return BaseAgent(Settings.load())


# 上下文窗口预算（token）。留一半给模型输出。
MAX_HISTORY_TOKENS = 4000
MAX_HISTORY_MESSAGES = 100


async def handle_message(channel: Channel, message: IncomingMessage) -> None:
    """把一条入站消息交给 Agent，并交回通道回复。

    短期记忆流程：
    1. 从 chat_history 加载该会话的历史（按 token 裁剪）
    2. 把历史 + 本轮 user 消息一起传给 Agent
    3. 拿到 assistant 回复后，回写 user + assistant 两条消息
    4. 清理超出 200 条的旧记录
    """
    conv_id = message.conversation_id or message.user_id
    log.info("收到 [%s] conversation=%s user=%s: %s",
             channel.name, conv_id, message.user_id, message.text[:200])

    # ① 加载历史（失败不阻断主流程，降级为单轮对话）
    try:
        history = hist.load_history(
            conv_id,
            max_tokens=MAX_HISTORY_TOKENS,
            max_messages=MAX_HISTORY_MESSAGES,
        )
        if history:
            log.info("加载到 %d 条历史消息 (conversation=%s)", len(history), conv_id)
    except Exception:
        log.exception("加载对话历史失败，降级为无记忆单轮")
        history = []

    try:
        await channel.send_typing(message, True)

        # ② 调 Agent：历史 messages + 本轮 user text
        answer = await channel.reply_stream(
            message,
            get_agent().astream(text=message.text, messages=history),
        )

        # ③ 成功后持久化两条消息（失败不阻断回复，日志告警）
        if answer:
            _save_conversation_pair(conv_id, message.user_id, message.text, answer)

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


def _save_conversation_pair(
    conversation_id: str,
    open_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """成对写 user + assistant 消息到 chat_history，非阻塞。"""
    try:
        hist.save_message(conversation_id, open_id, "user", user_text)
        hist.save_message(conversation_id, open_id, "assistant", assistant_text)
        hist.cleanup_old(conversation_id, keep=200)
    except Exception:  # noqa: BLE001
        log.exception("保存对话历史失败（不影响回复）")


def create_channel(name: str) -> Channel:
    if name == "feishu":
        from app.channels.feishu import FeishuChannel

        return FeishuChannel()
    raise SystemExit(f"未知通道：{name}（当前仅支持 feishu）")


async def run_channel(name: str) -> None:
    channel = create_channel(name)
    log.info("通道已启动：%s", channel.name)
    await channel.start(partial(handle_message, channel))
