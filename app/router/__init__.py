"""通道无关的消息处理与启动调度。"""

from __future__ import annotations

import logging
from functools import lru_cache, partial

from app.agents.base_agent import BaseAgent
from app.channels.base import Channel, IncomingMessage
from app.core.config import Settings

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


async def handle_message(channel: Channel, message: IncomingMessage) -> None:
    """把一条入站消息交给 Agent，并交回通道回复。"""
    log.info("收到 [%s] %s: %s", channel.name, message.user_id, message.text[:200])
    try:
        await channel.send_typing(message, True)
        await channel.reply_stream(message, get_agent().astream(message.text))
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
    channel = create_channel(name)
    log.info("通道已启动：%s", channel.name)
    await channel.start(partial(handle_message, channel))
