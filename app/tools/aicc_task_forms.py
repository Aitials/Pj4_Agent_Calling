"""外呼任务表单入口工具。

LLM 只负责识别「用户要建任务 / 启动任务」，然后调用这两个工具发卡片；
真正的参数由用户在卡片里填写，确认后再由卡片流程调用业务函数。
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

log = logging.getLogger("aicc_task_forms")

# 确定性意图校验：不依赖模型自觉，避免「11」这类含糊输入也弹表单
_CREATE_VERBS = ("创建", "新建", "建一个", "建个", "加一个", "加个", "搭建", "做个")
_START_VERBS = ("启动", "开始跑", "开始任务", "开跑", "跑起来", "执行任务")
_TASK_NOUNS = ("任务", "外呼", "机器人", "BOT", "bot", "话术")


def _has_intent(text: str, kind: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if kind == "create":
        return any(word in text for word in _CREATE_VERBS) and any(
            word in text for word in _TASK_NOUNS
        )
    return any(word in text for word in _START_VERBS)


def _open(kind: str) -> str:
    # 延迟导入：避免工具层与通道层在模块加载期互相依赖
    from app.channels.feishu import get_active_channel, get_current_message
    from app.flows import aicc_task_flow

    channel = get_active_channel()
    message = get_current_message()
    if channel is None or message is None:
        return "当前不在飞书会话中，无法发送表单卡片。"

    if not _has_intent(message.text, kind):
        action = "创建" if kind == "create" else "启动"
        return (
            f"用户当前这句话没有明确表达“{action}外呼任务”的意图，已取消发卡。"
            f"请先向用户确认是否要{action}任务，确认后再调用本工具。"
        )

    loop = getattr(channel, "_loop", None)
    if loop is None:
        return "飞书通道尚未就绪，请稍后重试。"

    action = "open_create" if kind == "create" else "open_start"
    asyncio.run_coroutine_threadsafe(
        aicc_task_flow.handle_action(
            channel,
            action,
            {},
            {},
            message.user_id,
            channel._chat_id(message),
            message.message_id,
        ),
        loop,
    )
    return "已发送表单卡片，请让用户在卡片中填写后提交。"


@tool
def open_create_task_form() -> str:
    """打开「创建外呼任务」表单卡片。

    仅当用户**明确表达**要创建、新建外呼任务时调用；含糊输入（数字、寒暄、追问）一律不要调用。
    调用后会发一张表单卡片，参数由用户在卡片里填写，二次确认后才会真正创建。
    不要自己编造参数，也不要直接调用创建接口。
    """
    return _open("create")


@tool
def open_start_task_form() -> str:
    """打开「启动外呼任务」表单卡片。

    仅当用户**明确表达**要启动、开始跑某个外呼任务时调用；含糊输入一律不要调用。
    卡片里会列出可启动状态的任务，也支持手填任务 ID；用户二次确认后才会真正启动。
    不要自己猜测任务 ID，也不要直接调用启动接口。
    """
    return _open("start")
