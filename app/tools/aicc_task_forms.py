"""外呼任务表单入口工具。

LLM 只负责识别「用户要建任务 / 启动任务」，然后调用这两个工具发卡片；
真正的参数由用户在卡片里填写，确认后再由卡片流程调用业务函数。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from langchain_core.tools import tool

log = logging.getLogger("aicc_task_forms")

# 同一条用户消息只允许发一次表单卡：模型重复调用时直接拒绝，避免刷屏
_OPEN_GUARD: dict[str, float] = {}
_OPEN_GUARD_LOCK = threading.Lock()
_OPEN_GUARD_TTL = 600.0

# 确定性意图校验：不依赖模型自觉，避免「11」这类含糊输入也弹表单
_CREATE_VERBS = ("创建", "新建", "建一个", "建个", "加一个", "加个", "搭建", "做个")
_START_VERBS = ("启动", "开始跑", "开始任务", "开跑", "跑起来", "执行任务")
_TASK_NOUNS = ("任务", "外呼", "机器人", "BOT", "bot", "话术")
_STATUS_WORDS = (
    "状态", "进度", "概况", "情况", "怎么样", "跑得", "进展", "完成多少", "运行", "跑到",
)


def _has_intent(text: str, kind: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if kind == "create":
        return any(word in text for word in _CREATE_VERBS) and any(
            word in text for word in _TASK_NOUNS
        )
    return any(word in text for word in _START_VERBS)


def _guard_key(message) -> str:
    message_id = str(getattr(message, "message_id", "") or "")
    if message_id:
        return message_id
    return f"{message.user_id}:{(message.text or '')[:50]}"


def _claim_open(key: str) -> bool:
    """首次调用返回 True，同一条消息的重复调用返回 False。"""
    now = time.monotonic()
    with _OPEN_GUARD_LOCK:
        for stale in [k for k, ts in _OPEN_GUARD.items() if now - ts > _OPEN_GUARD_TTL]:
            _OPEN_GUARD.pop(stale, None)
        if key in _OPEN_GUARD:
            return False
        _OPEN_GUARD[key] = now
        return True


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

    if not _claim_open(_guard_key(message)):
        log.warning("同一消息重复请求发卡，已忽略 user=%s", message.user_id)
        return (
            "本消息的表单卡片已发送过，禁止重复发送。"
            "请立即停止调用任何工具，直接用一句话回复用户，提醒其在卡片中填写并提交。"
        )

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
    return (
        "表单卡片已发送。请立即停止调用任何工具，"
        "直接用一句简短的话回复用户，提醒其在卡片中填写并提交。"
    )


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


def _has_status_intent(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if any(word in text for word in _STATUS_WORDS):
        return True
    return "任务" in text and any(word in text for word in ("查", "看", "多少", "如何"))


def _open_status(task_keyword: str) -> str:
    from app.channels.feishu import get_active_channel, get_current_message
    from app.flows import aicc_task_flow

    channel = get_active_channel()
    message = get_current_message()
    if channel is None or message is None:
        return "当前不在飞书会话中，无法发送任务状态卡片。"

    if not _has_status_intent(message.text):
        return (
            "用户当前这句话没有明确查询任务状态的意图，已取消发卡。"
            "请先向用户确认是否要查看某个外呼任务的运行情况。"
        )

    loop = getattr(channel, "_loop", None)
    if loop is None:
        return "飞书通道尚未就绪，请稍后重试。"

    asyncio.run_coroutine_threadsafe(
        aicc_task_flow.handle_action(
            channel,
            "open_status",
            {"task_keyword": (task_keyword or "").strip()},
            {},
            message.user_id,
            channel._chat_id(message),
            message.message_id,
        ),
        loop,
    )
    if (task_keyword or "").strip():
        return "已按任务名/ID 查询，若匹配到多个任务会先发任务选择卡片。"
    return "已发送任务筛选卡片，请让用户选择筛选条件后查看运行概况。"


@tool
def query_aicc_task_status(task_keyword: str = "") -> str:
    """查询一知 AI 外呼任务的运行概况（状态、进度、通话效果、意向分布）。

    Args:
        task_keyword: 用户明确提到的任务名称片段或任务 ID；用户没有提到具体任务时留空。

    用于回答「zlytest 这个任务跑得怎么样」「任务 1704345577064 现在什么状态」
    「最近有哪些任务在跑」这类问题。
    - 有关键词：先在真实任务列表里核对，唯一命中直接出概况卡，多个命中出候选卡；
    - 没有关键词：发筛选卡（状态 / 排序 / 创建时间范围），再选择具体任务。
    不要自己编造任务 ID，也不要假设任务状态。
    """
    return _open_status(task_keyword)
