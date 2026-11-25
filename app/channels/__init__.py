"""聊天通道层：通道注册表与「当前消息」上下文。

进程内只运行一个通道（CHANNEL 环境变量决定），但工具层需要知道
当前是哪个通道，才能过滤掉仅在飞书可用的工具。
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 避免循环导入
    from app.channels.base import Channel, IncomingMessage

_lock = threading.Lock()
_channels: dict[str, "Channel"] = {}

_current_message: ContextVar = ContextVar("current_message", default=None)


def register_channel(channel: "Channel") -> None:
    """通道实例化后登记，供工具层判断当前渠道能力。"""
    name = str(getattr(channel, "name", "") or "")
    if not name:
        return
    with _lock:
        _channels[name] = channel


def get_channel(name: str = "") -> "Channel | None":
    with _lock:
        if name:
            return _channels.get(name)
        return next(iter(_channels.values()), None)


def get_active_channel() -> "Channel | None":
    """当前运行的通道实例。"""
    return get_channel()


def get_active_channel_name() -> str:
    channel = get_active_channel()
    return str(getattr(channel, "name", "") or "")


def set_current_message(message: "IncomingMessage"):
    """处理一条消息前绑定上下文，返回可用于 reset 的 token。"""
    return _current_message.set(message)


def reset_current_message(token) -> None:
    try:
        _current_message.reset(token)
    except (ValueError, RuntimeError):
        # 跨上下文 reset 会失败，忽略即可
        pass


def get_current_message() -> "IncomingMessage | None":
    return _current_message.get()
