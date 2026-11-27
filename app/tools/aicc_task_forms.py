"""外呼任务表单入口工具。

LLM 只负责识别「用户要建任务 / 启动任务」，然后调用这两个工具发卡片；
真正的参数由用户在卡片里填写，确认后再由卡片流程调用业务函数。
"""

from __future__ import annotations

import asyncio
import logging
import re
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
_PAUSE_VERBS = ("暂停", "停止", "停掉", "先停")
_TASK_NOUNS = ("任务", "外呼", "机器人", "BOT", "bot", "话术")
_OPEN_ACTIONS = {"create": "open_create", "start": "open_start", "pause": "open_pause"}
_ACTION_LABELS = {"create": "创建", "start": "启动", "pause": "暂停"}
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
    if kind == "pause":
        return _has_pause_intent(text)
    return any(word in text for word in _START_VERBS)


def _has_pause_intent(text: str) -> bool:
    """暂停必须“明确指向任务”：只有动词没有对象时不发卡。

    满足任一即可：
    - 停类动词 + 任务名词，如「把昨天的回访任务停掉」；
    - 停类动词 + 任务 ID，如「暂停 1704345203463」。
    仅说「暂停」「先停一下」这类没有任务对象的表述，交给模型先反问。
    """
    if not any(verb in text for verb in _PAUSE_VERBS):
        return False
    if any(noun in text for noun in _TASK_NOUNS):
        return True
    return bool(re.search(r"(暂停|停止|停掉)\s*\d{6,}", text))


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
    from app.channels import get_active_channel, get_current_message
    from app.flows import aicc_task_flow

    channel = get_active_channel()
    message = get_current_message()
    if (
        channel is None
        or message is None
        or not getattr(channel, "supports_cards", False)
    ):
        return "当前渠道暂不支持表单卡片。"

    if not _has_intent(message.text, kind):
        action = _ACTION_LABELS.get(kind, "操作")
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

    action = _OPEN_ACTIONS.get(kind, "open_create")
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


@tool(parse_docstring=True)
def open_create_task_form() -> str:
    """打开「创建外呼任务」表单卡片（不会直接创建任务）。

    何时使用：用户明确表达「创建/新建/建一个外呼任务」，例如「帮我建个外呼任务」。
    不要使用：寒暄、纯数字、追问、上下文不完整；启动任务或查询状态也不用本工具。
              含糊输入先一句话反问确认，不要先发卡。
    参数：无。任务名、话术、线路、并发、拨打时段等全部由用户在卡片里填写，不要编造。
    副作用：会向当前会话发送一张表单卡片；同一条消息最多发一次，重复调用会被拒绝。
            不会真正创建任务，用户确认后才由卡片流程创建。
    返回：发卡结果提示；成功后应立即停止调用工具，用一句话提醒用户填写并提交。
    """
    return _open("create")


@tool(parse_docstring=True)
def open_start_task_form() -> str:
    """打开「启动外呼任务」表单卡片（不会直接启动任务）。

    何时使用：用户明确表达「启动/开始跑某个外呼任务」，例如「把昨天的任务启动起来」。
    不要使用：查询状态、创建任务、寒暄或含糊输入；不确定要启动哪个任务时先反问。
    参数：无。卡片会列出可启动任务，也支持手填任务 ID；不要自己猜测任务 ID。
    副作用：会向当前会话发送一张表单卡片；同一条消息最多发一次。
            不会真正启动任务，用户确认后才由卡片流程启动。
    返回：发卡结果提示；成功后应立即停止调用工具，用一句话提醒用户选择并确认。
    """
    return _open("start")


@tool(parse_docstring=True)
def open_pause_task_form() -> str:
    """打开「暂停外呼任务」表单卡片（不会直接暂停任务）。

    何时使用：用户明确要求停掉某个外呼任务，例如「把昨天的回访任务暂停」「停掉任务 1704345203463」。
    不要使用：只说了「暂停」「先停一下」但没指向任何任务；查询状态、创建或启动任务；
              也不要自己猜测任务 ID，不确定要停哪个任务时先反问。
              本工具只做「暂停/停止任务」，用户要的是「终止」时不要调用。
    参数：无。卡片会列出进行中 / 排队中 / 可运行的任务，也支持手填任务 ID。
    副作用：会向当前会话发送一张表单卡片；同一条消息最多发一次。
            不会真正暂停任务，用户确认后才执行。
    返回：发卡结果提示；成功后应立即停止调用工具，用一句话提醒用户选择并确认。
    """
    return _open("pause")


def _has_status_intent(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if any(word in text for word in _STATUS_WORDS):
        return True
    return "任务" in text and any(word in text for word in ("查", "看", "多少", "如何"))


def _open_status(task_keyword: str) -> str:
    from app.channels import get_active_channel, get_current_message
    from app.flows import aicc_task_flow

    channel = get_active_channel()
    message = get_current_message()
    if (
        channel is None
        or message is None
        or not getattr(channel, "supports_cards", False)
    ):
        return "当前渠道暂不支持任务状态卡片。"

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


@tool(parse_docstring=True)
def query_aicc_task_status(task_keyword: str = "") -> str:
    """打开某个外呼任务的运行概况卡片（状态、进度、通话效果、意向分布）。

    注意：本工具会发卡片，不只是返回数据；卡片才是给用户看的最终内容。
    何时使用：用户问某个具体任务「跑得怎么样」「现在什么状态」「进度多少」，
              或问「最近有哪些任务在跑」需要先选任务。
    不要使用：问整体看板/接通率（用 query_aicc_dashboard）；问客户资料（用 query_customer_profile）；
              寒暄或没有任务语义时不要调用。
    副作用：会向当前会话发送任务筛选卡或概况卡。
    返回：发卡结果提示；关键词命中多个任务时会先发选择卡。

    Args:
        task_keyword: 用户明确提到的任务名称片段或任务 ID；用户没提就留空，不要猜测或编造。
    """
    return _open_status(task_keyword)
