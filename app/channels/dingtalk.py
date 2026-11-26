"""钉钉企业内部应用机器人通道（Stream 模式，阶段 1：文本对话）。

Stream 模式免公网回调、免 AES 加解密：SDK 主动连钉钉网关，
订阅机器人消息 topic `/v1.0/im/bot/messages/get`。
回调里的 senderStaffId 就是明文 userId，会话记忆直接按它隔离。
回答通过 AI 互动卡片流式输出（ai_start → ai_streaming → ai_finish），
卡片不可用或中途失败时回退为「处理中提示 + 完成后整段结果」。

注意：事件订阅（EVENT）与机器人消息（CALLBACK）是两条独立通道。
后台「事件订阅 → Stream 模式推送 → 验证连接通道」校验的是 EVENT 通道，
因此除 chatbot handler 外必须再注册 EventHandler，否则验证失败、无法保存推送方式。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncIterator

import dingtalk_stream  # noqa: F401  SDK 日志/类型来源
import httpx
from dingtalk_stream import (
    AckMessage,
    AICardStatus,
    CallbackHandler,
    CallbackMessage,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
    EventHandler,
    EventMessage,
)
from dingtalk_stream.card_callback import (
    Card_Callback_Router_Topic,
    CardCallbackMessage,
)
from dingtalk_stream.card_instance import AIMarkdownCardInstance

from app.channels import reset_current_message, set_current_message
from app.channels.base import Channel, IncomingMessage, MessageHandler
from app.clients import dingtalk_api
from app.core import forms
from app.core.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET

log = logging.getLogger("dingtalk")

# 回答超过该时长仍未完成时，先回一条「处理中」（仅回退路径使用）
PROCESSING_HINT_DELAY = 4.0
MAX_SEEN_IDS = 500
_SEEN_TTL_SECONDS = 600.0
_WEBHOOK_TIMEOUT_SECONDS = 5.0

# ===== AI 卡片流式参数（2026-11-25 实测校准）=====
CARD_MIN_INTERVAL = 0.6       # 两次推流的最小间隔（秒）
CARD_MIN_CHARS = 20           # 单次推流的最少新增字数
CARD_MAX_UPDATES = 80         # 单张卡片最多推流次数
CARD_MAX_SECONDS = 120.0      # 单张卡片最长推流时长
CARD_HINT_INTERVAL = 8.0      # 无内容时的进度提示间隔
CARD_SLOW_THRESHOLD = 1.0     # 单次推流超过该耗时视为网络慢
CARD_RECOVER_STREAK = 2       # 连续正常多少次后恢复推流间隔
CARD_FAIL_LIMIT = 3           # 连续失败多少次后放弃卡片、改用文本兜底
CARD_DRAIN_SECONDS = 10.0     # 收尾前等待在途推流落地的最长时间
BUTTON_DEDUP_SECONDS = 2.0    # 同一张卡同一按钮的重复回调窗口（平台重投/连点）
MAX_SEEN_BUTTONS = 500


class _BotHandler(ChatbotHandler):
    """SDK 回调入口：只做分发，立刻 ACK，避免 Stream 侧判定超时。"""

    def __init__(self, channel: "DingtalkChannel") -> None:
        super().__init__()
        self._channel = channel

    async def process(self, callback: CallbackMessage):
        try:
            await self._channel.on_callback(callback)
        except Exception:  # noqa: BLE001
            log.exception("处理钉钉回调失败")
        return AckMessage.STATUS_OK, "OK"


class _EventHandler(EventHandler):
    """事件订阅处理器：让 SDK 上报 EVENT: * 订阅，供后台验证连接通道。

    后台「事件订阅 → 验证连接通道」会向本应用在线的 Stream 连接推送一条
    测试事件，只有客户端订阅了 EVENT 且返回 ACK(code=200) 才判定接入成功。
    本期业务用不到具体事件内容，统一记录并 ACK 即可；不注册本 handler 时
    客户端订阅里只有 CALLBACK，后台必然报「接入失败」且无法保存推送方式。
    """

    async def process(self, event: EventMessage):
        topic = getattr(getattr(event, "headers", None), "topic", "")
        log.info("收到钉钉事件订阅推送 topic=%s", topic)
        return AckMessage.STATUS_OK, "OK"


class _ObservableAiCard(AIMarkdownCardInstance):
    """覆盖 SDK 的两个网络方法，让推流/收尾结果可判断（SDK 原生只打日志）。"""

    def __init__(self, client, incoming) -> None:
        super().__init__(client, incoming)
        self.last_stream_ok = True
        self.last_update_ok = True

    def streaming(
        self, card_instance_id, content_key, content_value, append, finished, failed
    ):
        ok = dingtalk_api.stream_card(
            card_instance_id,
            content_value,
            append=append,
            finalize=bool(finished),
        )
        self.last_stream_ok = ok
        return ok

    def ai_streaming_final(self, text: str) -> None:
        """收尾前的最后一次全量正文：进入输入态 + isFinalize=True 结束打字机。

        SDK 的 ai_streaming 固定 finished=False，无法结束流式；而卡片正文
        只由流式通道渲染，只在 ai_finish 里写 cardParamMap 不会补上正文。
        """
        if not self.card_instance_id:
            self.logger.error(
                "AIMarkdownCardInstance.ai_streaming_final failed, "
                "you should send card first."
            )
            self.last_stream_ok = False
            return
        if not self.inputing_status:
            self.put_card_data(
                self.card_instance_id, self.get_card_data(AICardStatus.INPUTING)
            )
            self.inputing_status = True
        self.markdown = text
        self.streaming(
            self.card_instance_id,
            "msgContent",
            self.markdown,
            append=False,
            finished=True,
            failed=False,
        )

    def put_card_data(self, card_instance_id: str, card_data: dict, **kwargs):
        ok = dingtalk_api.update_card(card_instance_id, card_data)
        self.last_update_ok = ok
        return ok


class _AnswerCard:
    """一次回答的 AI 卡片生命周期：建卡 → 节流推流 → 收尾。

    采集与推送解耦：feed() 只更新最新全文并唤醒推送协程，
    推送协程串行调用 OpenAPI，永远发送当前全量文本，避免乱序。
    """

    def __init__(
        self,
        client,
        incoming,
        *,
        title: str = "AI 助手",
        card_factory=None,
        min_interval: float = CARD_MIN_INTERVAL,
        min_chars: int = CARD_MIN_CHARS,
        max_updates: int = CARD_MAX_UPDATES,
        max_seconds: float = CARD_MAX_SECONDS,
        hint_interval: float = CARD_HINT_INTERVAL,
    ) -> None:
        factory = card_factory or _ObservableAiCard
        self._card = factory(client, incoming)
        self._card.set_title_and_logo(title, "")

        self._latest = ""
        self._sent = ""
        self._event = asyncio.Event()
        self._wake = asyncio.Event()
        self._closed = False
        self._broken = False
        self._updates = 0
        self._fail_streak = 0
        self._ok_streak = 0
        self._base_interval = max(0.05, float(min_interval))
        self._interval = self._base_interval
        self._min_chars = max(1, int(min_chars))
        self._max_updates = max(1, int(max_updates))
        self._max_seconds = max(1.0, float(max_seconds))
        self._hint_interval = max(0.5, float(hint_interval))
        self._last_push = 0.0
        self._last_cost = 0.0
        self._started = 0.0
        self._push_task: asyncio.Task | None = None
        self._hint_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ 生命周期

    async def start(self) -> bool:
        try:
            await asyncio.to_thread(self._card.ai_start)
        except Exception:  # noqa: BLE001
            log.exception("钉钉 AI 卡片创建异常")
            return False
        if not getattr(self._card, "card_instance_id", ""):
            return False

        self._started = time.monotonic()
        self._push_task = asyncio.create_task(self._push_loop())
        self._hint_task = asyncio.create_task(self._hint_loop())
        return True

    def feed(self, text: str) -> None:
        if self._closed:
            return
        self._latest = text
        self._event.set()

    async def finish(self, text: str) -> bool:
        drained = await self._stop()
        if self._broken:
            return False
        if not drained:
            # 在途推流没落地就收尾，旧快照会覆盖正文，交给文本兜底更可靠
            log.warning("钉钉卡片推流未排空，放弃卡片收尾改用文本兜底")
            return False
        # 正文只由流式通道渲染，ai_finish 的 cardParamMap 补不上正文；
        # 必须用「全量 + finalize」再推一次，否则尾部内容会缺失。
        if text.strip() and not await self._push(text, finalize=True):
            return False
        self._card.last_update_ok = True
        try:
            await asyncio.to_thread(self._card.ai_finish, text)
        except Exception:  # noqa: BLE001
            log.exception("钉钉卡片收尾异常")
            return False
        return getattr(self._card, "last_update_ok", True)

    async def fail(self) -> None:
        await self._stop()
        try:
            await asyncio.to_thread(self._card.ai_fail)
        except Exception:  # noqa: BLE001
            log.exception("钉钉卡片标记失败态异常")

    # ------------------------------------------------------------------ 推送

    async def _push_loop(self) -> None:
        while not self._closed:
            await self._event.wait()
            self._event.clear()
            while not self._closed:
                text = self._latest
                if text == self._sent:
                    break
                now = time.monotonic()
                if (
                    now - self._started >= self._max_seconds
                    or self._updates >= self._max_updates
                ):
                    # 达到上限后停止中间推流；收尾会用「全量 + finalize」补齐
                    break
                if now - self._last_push < self._interval:
                    break
                if len(text) - len(self._sent) < self._min_chars:
                    break

                if not await self._push(text):
                    self._fail_streak += 1
                    self._ok_streak = 0
                    if self._fail_streak >= CARD_FAIL_LIMIT:
                        self._broken = True
                        log.warning("钉钉卡片连续推流失败，改用文本兜底")
                        return
                    # 失败后稍作退避，避免无内容变化时紧循环重试
                    await asyncio.sleep(min(self._interval, 1.0))
                    continue

                self._fail_streak = 0
                self._sent = text
                self._updates += 1
                self._adapt_interval()
                if self._latest == text:
                    break

    async def _hint_loop(self) -> None:
        """首字前的进度提示，避免十几秒只有一张空卡片。"""
        while not self._closed and not self._sent:
            try:
                # 可中断休眠：收尾时置位 _wake，避免 cancel 打断在途请求
                await asyncio.wait_for(
                    self._wake.wait(), timeout=self._hint_interval
                )
                return
            except asyncio.TimeoutError:
                pass
            if self._closed or self._sent:
                return
            elapsed = int(time.monotonic() - self._started)
            await self._push(f"正在思考…（已用时 {elapsed} 秒）")

    async def _push(self, text: str, *, finalize: bool = False) -> bool:
        async with self._lock:
            started = time.monotonic()
            try:
                if finalize:
                    call = getattr(self._card, "ai_streaming_final", None)
                    if call is None:
                        call = self._card.ai_streaming
                    await asyncio.to_thread(call, text)
                else:
                    await asyncio.to_thread(self._card.ai_streaming, text)
            except Exception:  # noqa: BLE001
                log.exception("钉钉卡片推流异常")
                return False
            self._last_cost = time.monotonic() - started
            self._last_push = time.monotonic()
        return getattr(self._card, "last_stream_ok", True)

    def _adapt_interval(self) -> None:
        """网络抖动时放大间隔，连续正常后逐步恢复。"""
        if self._last_cost >= CARD_SLOW_THRESHOLD:
            self._ok_streak = 0
            self._interval = min(self._interval * 2, 4.0)
            return
        self._ok_streak += 1
        if self._ok_streak >= CARD_RECOVER_STREAK:
            self._interval = max(self._base_interval, self._interval / 2)
            self._ok_streak = 0

    async def _stop(self) -> bool:
        """停止推流并等待在途请求落地；返回 False 表示未排空（勿再写终态）。"""
        self._closed = True
        self._event.set()
        self._wake.set()

        drained = True
        if self._hint_task is not None:
            try:
                await asyncio.wait_for(
                    self._hint_task, timeout=CARD_DRAIN_SECONDS
                )
            except asyncio.TimeoutError:
                self._hint_task.cancel()
                drained = False
            except asyncio.CancelledError:
                drained = False
            except Exception:  # noqa: BLE001
                log.exception("钉钉卡片进度提示任务异常")
                drained = False
        if self._push_task is not None:
            try:
                await asyncio.wait_for(
                    self._push_task, timeout=CARD_DRAIN_SECONDS
                )
            except asyncio.TimeoutError:
                self._push_task.cancel()
                drained = False
            except asyncio.CancelledError:
                drained = False
            except Exception:  # noqa: BLE001
                log.exception("钉钉卡片推流任务异常")
                drained = False
        return drained


# ---------------------------------------------------------------------------
# 卡片回调：表单值归一化与按钮 id 解析
# ---------------------------------------------------------------------------

# 按钮 id 协议：<action>|<token>[|<extra>]；extra 按动作落到不同字段
_EXTRA_VALUE_KEYS = {
    "task_status_pick": "job_id",
    "task_status_refresh": "job_id",
    "task_status_page": "page",
    "task_status_back": "to",
    "task_form_refresh": "kind",
}

# 这些动作完成后把原卡收敛成「已处理」态
_CLOSE_CARD_ACTIONS = {
    "task_create_confirm",
    "task_start_confirm",
    "task_create_skip",
    "task_form_cancel",
}


def _normalize_form_value(field_type: str, value):
    """把模板表单的回传值转成业务层能直接用的类型。"""
    kind = str(field_type or "").upper()
    if kind == "MULTI_SELECT":
        if isinstance(value, dict):
            return list(value.get("value") or [])
        return value if isinstance(value, list) else []
    if kind == "SELECT":
        if isinstance(value, dict):
            return value.get("value")
        return value
    if kind == "NUMBER":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        text = str(value or "").strip()
        try:
            number = float(text)
        except ValueError:
            return value
        return int(number) if number.is_integer() else number
    if kind in ("CHECKBOX", "SWITCH"):
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in ("true", "1", "yes", "是")
    if kind in ("TEXT", "TEXT_AREA", "DATE", "TIME", "DATETIME"):
        return "" if value is None else str(value)
    return value


def _normalize_form_params(params: dict) -> dict:
    """按 current_form.fields[].type 归一化整份表单。"""
    form = params.get("form") if isinstance(params.get("form"), dict) else {}
    current = (
        params.get("current_form")
        if isinstance(params.get("current_form"), dict)
        else {}
    )
    types: dict[str, str] = {}
    for field in current.get("fields") or []:
        if isinstance(field, dict) and field.get("name"):
            types[str(field["name"])] = str(field.get("type") or "")
    return {
        str(key): _normalize_form_value(types.get(str(key), ""), value)
        for key, value in form.items()
    }


def _parse_button_id(raw: str) -> tuple[str, str, str]:
    action, _, rest = str(raw or "").partition("|")
    token, _, extra = rest.partition("|")
    return action.strip(), token.strip(), extra.strip()


class _CardCallbackHandler(CallbackHandler):
    """卡片回调入口：只做分发，异常也必须 ACK，避免平台重试风暴。"""

    def __init__(self, channel: "DingtalkChannel") -> None:
        super().__init__()
        self._channel = channel

    async def process(self, callback: CallbackMessage):
        try:
            return await self._channel.on_card_callback(callback)
        except Exception:  # noqa: BLE001
            log.exception("处理钉钉卡片回调失败")
            return AckMessage.STATUS_OK, "OK"


def _card_message_shell(user_id: str) -> ChatbotMessage:
    """主动发卡用的最小消息壳：只提供卡片投放需要的身份字段。"""
    message = ChatbotMessage()
    message.sender_staff_id = user_id
    message.sender_nick = "任务卡片"
    message.sender_id = "task-card"
    message.sender_corp_id = "task-card"
    message.conversation_id = f"cid-task-{user_id}"
    message.conversation_type = "1"
    message.message_id = f"task-card-{uuid.uuid4().hex}"
    message.chatbot_user_id = "task-card"
    return message


class DingtalkChannel(Channel):
    name = "dingtalk"
    supports_cards = True

    def __init__(self) -> None:
        self._client: DingTalkStreamClient | None = None
        self._handler: MessageHandler | None = None
        self._form_handler = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seen: dict[str, float] = {}
        self._seen_buttons: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        self._card_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 启动

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        self._loop = asyncio.get_running_loop()
        if not dingtalk_api.is_configured():
            raise RuntimeError(
                "缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请在 .env 中配置"
            )

        client = DingTalkStreamClient(
            Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
            logger=logging.getLogger("dingtalk_stream"),
        )
        client.register_callback_handler(ChatbotMessage.TOPIC, _BotHandler(self))
        client.register_callback_handler(
            Card_Callback_Router_Topic, _CardCallbackHandler(self)
        )
        # 关键：注册事件订阅 handler，使建连时的 subscriptions 带上 EVENT: *，
        # 后台「验证连接通道」才能通过，事件订阅推送方式才能保存。
        client.register_all_event_handler(_EventHandler())
        self._client = client

        log.info(
            "钉钉 Stream 客户端已启动，订阅 CALLBACK topic=%s + 卡片 %s + EVENT:*",
            ChatbotMessage.TOPIC,
            Card_Callback_Router_Topic,
        )
        # SDK 内部自带断线重连，异常不会向外抛
        await client.start()

    def set_form_handler(self, handler) -> None:
        """注入任务卡片流程处理函数（router 负责绑定 channel）。"""
        self._form_handler = handler

    def _chat_id(self, message: IncomingMessage) -> str:
        return message.conversation_id or message.user_id

    # ------------------------------------------------------------------ 收消息

    async def on_callback(self, callback: CallbackMessage) -> None:
        data = callback.data if isinstance(callback.data, dict) else {}
        # 阶段 1 用于确认真实回调帧结构，排障时打开
        log.debug("钉钉回调原始载荷：%s", str(data)[:1000])

        try:
            incoming = ChatbotMessage.from_dict(data)
        except Exception:  # noqa: BLE001
            # SDK 对未知/不完整载荷会直接抛 KeyError，丢弃即可，不能拖垮回调
            log.exception("解析钉钉回调失败 payload=%s", str(data)[:500])
            return
        msg_id = str(getattr(incoming, "message_id", "") or "")
        if msg_id and self._is_duplicate(msg_id):
            log.info("重复消息已忽略 msgId=%s", msg_id)
            return

        # 群聊本期关闭，只做单聊
        if str(incoming.conversation_type or "1") == "2":
            log.info("钉钉群聊消息已忽略 conversation=%s", incoming.conversation_id)
            return

        user_id = str(getattr(incoming, "sender_staff_id", "") or "").strip()
        if not user_id:
            log.warning("钉钉消息缺少 senderStaffId，已忽略 payload=%s", str(data)[:300])
            return

        text, prompt = self._extract_text(incoming)
        if prompt:
            await self._safe_reply(incoming, prompt, markdown=False)
            return
        if not text:
            return

        message = IncomingMessage(
            channel=self.name,
            message_id=msg_id,
            user_id=user_id,
            # 单聊下会话收敛到发送人明文 userId，thread_id 与身份一致
            conversation_id=user_id,
            text=text,
            raw=data,
            reply_handle=incoming,
        )
        if self._handler is None:
            return
        # Agent 处理耗时较长，放到后台任务，回调先 ACK
        task = asyncio.create_task(self._run_handler(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_handler(self, message: IncomingMessage) -> None:
        """在任务内绑定当前消息上下文，供工具层读取。"""
        token = set_current_message(message)
        try:
            if self._handler is not None:
                await self._handler(message)
        finally:
            reset_current_message(token)

    # ------------------------------------------------------------------ 卡片回调

    async def on_card_callback(self, callback: CallbackMessage):
        """卡片回调：解析后立即返回 ACK，业务处理放后台任务。"""
        data = callback.data if isinstance(callback.data, dict) else {}
        topic = getattr(getattr(callback, "headers", None), "topic", "")
        try:
            msg = CardCallbackMessage.from_dict(data)
        except Exception:  # noqa: BLE001
            log.exception(
                "解析钉钉卡片回调失败 topic=%s payload=%s", topic, str(data)[:500]
            )
            return AckMessage.STATUS_OK, "OK"

        content = msg.content if isinstance(msg.content, dict) else {}
        private = content.get("cardPrivateData")
        params = private.get("params") if isinstance(private, dict) else {}
        params = params if isinstance(params, dict) else {}
        operator = str(msg.user_id or "").strip()
        if not operator:
            log.warning(
                "钉钉卡片回调缺少 userId，已忽略 outTrackId=%s", msg.card_instance_id
            )
            return AckMessage.STATUS_OK, "OK"

        form = params.get("form")
        if isinstance(form, dict):
            log.info(
                "钉钉表单回调 operator=%s outTrackId=%s fields=%s",
                operator,
                msg.card_instance_id,
                list(form.keys()),
            )
            return self._handle_form_callback(msg, params, form, operator)

        log.info(
            "钉钉按钮回调 operator=%s outTrackId=%s buttonId=%s",
            operator,
            msg.card_instance_id,
            params.get("id"),
        )
        return self._handle_button_callback(msg, params, operator)

    def _handle_form_callback(self, msg, params: dict, form: dict, operator: str):
        current = (
            params.get("current_form")
            if isinstance(params.get("current_form"), dict)
            else {}
        )
        form_value = _normalize_form_params(params)
        token = str(form_value.pop("token", "") or "")
        action = str(form_value.pop("__action", "") or "")
        if not token or not action:
            log.warning(
                "钉钉表单回调缺少 token/__action，已忽略 outTrackId=%s keys=%s",
                msg.card_instance_id,
                list(form.keys()),
            )
            return AckMessage.STATUS_OK, "OK"

        self._dispatch_action(
            action, {"token": token}, form_value, operator, msg.card_instance_id
        )
        # 响应内把表单置为已提交，禁止重复提交
        return AckMessage.STATUS_OK, dingtalk_api.form_submitted_response(form, current)

    def _handle_button_callback(self, msg, params: dict, operator: str):
        action, token, extra = _parse_button_id(str(params.get("id") or ""))
        if not action or not token:
            # 卡片进入客户端重载态时 params 可能只剩 text，按钮 id 丢失。
            # 明确提示用户重新发起，不再静默丢弃造成「点了没反应」。
            log.warning(
                "钉钉按钮回调缺少 action/token，已忽略 outTrackId=%s params=%s",
                msg.card_instance_id,
                params,
            )
            if operator:
                self._dispatch_text(operator, "这张卡片已失效，请重新发起任务状态查询。")
            return AckMessage.STATUS_OK, "OK"

        button_id = str(params.get("id") or "")
        if self._is_duplicate_button(f"{msg.card_instance_id}|{button_id}"):
            log.info(
                "重复按钮回调已忽略 outTrackId=%s buttonId=%s",
                msg.card_instance_id,
                button_id,
            )
            return AckMessage.STATUS_OK, "OK"

        if action == "task_form_cancel":
            forms.pop_for(token, operator)
            self._schedule_close_card(msg.card_instance_id, "已取消")
            return AckMessage.STATUS_OK, "OK"

        value: dict = {"token": token}
        extra_key = _EXTRA_VALUE_KEYS.get(action)
        if extra_key and extra:
            value[extra_key] = extra

        self._dispatch_action(action, value, {}, operator, msg.card_instance_id)
        if action in _CLOSE_CARD_ACTIONS:
            self._schedule_close_card(msg.card_instance_id, "已处理，请查看最新消息")
        elif action in ("task_status_pick", "task_status_refresh"):
            # 原卡随 ACK 直接置为无按钮失效态：不再另起 update_card 异步任务，
            # 避免与「发新概况卡」并发写同一张卡导致客户端进入重载态。
            # 业务层的卡片一次性闸门保证只认第一次点击。
            return (
                AckMessage.STATUS_OK,
                dingtalk_api.button_card_consumed_response("已提交，正在读取任务概况…"),
            )
        return AckMessage.STATUS_OK, "OK"

    def _dispatch_action(
        self,
        action: str,
        value: dict,
        form_value: dict,
        operator: str,
        card_instance_id: str,
    ) -> None:
        handler = self._form_handler
        if handler is None:
            log.warning("任务卡片处理器未注入，忽略 action=%s", action)
            return
        task = asyncio.create_task(
            handler(
                action,
                value,
                form_value,
                operator,
                operator,
                card_instance_id or "",
            )
        )
        self._card_tasks.add(task)
        task.add_done_callback(self._card_tasks.discard)

    def _schedule_close_card(self, card_instance_id: str, text: str) -> None:
        task = asyncio.create_task(self._close_card(card_instance_id, text))
        self._card_tasks.add(task)
        task.add_done_callback(self._card_tasks.discard)

    def _dispatch_text(self, user_id: str, text: str) -> None:
        """回调同步路径里安全地追加一条异步文本推送。"""
        task = asyncio.create_task(self.send_text_to_chat(user_id, text))
        self._card_tasks.add(task)
        task.add_done_callback(self._card_tasks.discard)

    async def _close_card(self, card_instance_id: str, text: str) -> None:
        if not card_instance_id:
            return
        ok = await asyncio.to_thread(
            dingtalk_api.update_card,
            card_instance_id,
            dingtalk_api.ai_card_text_data("任务卡片", text),
        )
        if not ok:
            log.warning("钉钉卡片收敛失败 outTrackId=%s", card_instance_id)

    @staticmethod
    def _extract_text(incoming: ChatbotMessage) -> tuple[str, str]:
        """返回 (文本, 提示语)；提示语非空表示该消息不进入 Agent。"""
        msgtype = str(incoming.message_type or "")
        if msgtype == "text":
            content = getattr(getattr(incoming, "text", None), "content", "") or ""
            return str(content).strip(), ""
        if not msgtype:
            return "", ""
        return "", "暂不支持图片/文件/语音等消息，请发送文字。"

    def _is_duplicate(self, msgid: str) -> bool:
        now = time.monotonic()
        for key, ts in list(self._seen.items()):
            if now - ts > _SEEN_TTL_SECONDS:
                self._seen.pop(key, None)
        if msgid in self._seen:
            return True
        self._seen[msgid] = now
        if len(self._seen) > MAX_SEEN_IDS:
            oldest = min(self._seen, key=self._seen.get)
            self._seen.pop(oldest, None)
        return False

    def _is_duplicate_button(self, key: str) -> bool:
        """按钮回调短窗口去重：平台重投与用户连点都只处理第一次。"""
        now = time.monotonic()
        for item, ts in list(self._seen_buttons.items()):
            if now - ts > BUTTON_DEDUP_SECONDS:
                self._seen_buttons.pop(item, None)
        if key in self._seen_buttons:
            return True
        self._seen_buttons[key] = now
        if len(self._seen_buttons) > MAX_SEEN_BUTTONS:
            oldest = min(self._seen_buttons, key=self._seen_buttons.get)
            self._seen_buttons.pop(oldest, None)
        return False

    # ------------------------------------------------------------------ 回复

    async def _post_webhook(
        self, incoming: ChatbotMessage, text: str, markdown: bool
    ) -> bool:
        """用回调里的 sessionWebhook 回复；无地址直接返回 False。"""
        url = str(getattr(incoming, "session_webhook", "") or "")
        if not url:
            return False
        payload = (
            {
                "msgtype": "markdown",
                "markdown": {"title": "AI 助手", "text": text or "（空消息）"},
            }
            if markdown
            else {"msgtype": "text", "text": {"content": text or "（空消息）"}}
        )
        async with httpx.AsyncClient(
            timeout=_WEBHOOK_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            response = await client.post(url, json=payload)
            data = response.json() if response.content else {}
        errcode = data.get("errcode") if isinstance(data, dict) else None
        if errcode not in (0, None):
            raise RuntimeError(
                f"sessionWebhook 回复失败：errcode={errcode} "
                f"errmsg={data.get('errmsg') if isinstance(data, dict) else ''}"
            )
        return True

    async def _safe_reply(self, incoming: ChatbotMessage, text: str, markdown: bool) -> None:
        """优先 sessionWebhook（带时效），失败降级为 OpenAPI 主动推送。"""
        try:
            if await self._post_webhook(incoming, text, markdown):
                return
        except Exception:  # noqa: BLE001
            log.exception("钉钉 sessionWebhook 回复失败，尝试主动推送")

        user_id = str(getattr(incoming, "sender_staff_id", "") or "").strip()
        if not user_id:
            log.error("钉钉回复失败：既无可用 webhook 也无 senderStaffId")
            return
        try:
            await asyncio.to_thread(
                dingtalk_api.send_bot_message, [user_id], text, markdown=markdown
            )
        except Exception:  # noqa: BLE001
            log.exception("钉钉主动推送回复也失败 user=%s", user_id)

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        raw = message.reply_handle
        if isinstance(raw, ChatbotMessage):
            await self._safe_reply(raw, text, markdown=True)
            return
        await self.send_text_to_chat(message.conversation_id, text)

    async def reply_stream(
        self, message: IncomingMessage, chunks: AsyncIterator[str]
    ) -> str:
        """优先用 AI 卡片流式输出；卡片不可用时回退为整段文本。"""
        raw = message.reply_handle
        if not isinstance(raw, ChatbotMessage) or self._client is None:
            return await self._reply_plain_stream(message, chunks)

        card = _AnswerCard(self._client, raw)
        if not await card.start():
            log.warning(
                "钉钉 AI 卡片创建失败，回退文本回复 conversation=%s",
                message.conversation_id,
            )
            return await self._reply_plain_stream(message, chunks)

        answer = ""
        try:
            async for delta in chunks:
                answer += delta
                card.feed(answer)
        except Exception:  # noqa: BLE001
            log.exception("钉钉卡片流式生成中断")
            partial = (
                answer + "\n\n（生成中断，请重试）"
                if answer.strip()
                else "生成中断，请重试"
            )
            if not await card.finish(partial):
                await self._safe_reply(raw, partial, markdown=True)
            return answer

        if not answer.strip():
            await card.finish("（没有生成内容）")
            return ""

        if not await card.finish(answer):
            log.warning(
                "钉钉卡片收尾失败，补发文本全文 conversation=%s",
                message.conversation_id,
            )
            await self._safe_reply(raw, answer, markdown=True)
        return answer

    async def _reply_plain_stream(
        self, message: IncomingMessage, chunks: AsyncIterator[str]
    ) -> str:
        """回退路径：4 秒处理中提示 + 聚合后整段发送。"""
        raw = message.reply_handle
        state = {"done": False}
        hint_task: asyncio.Task | None = None
        if isinstance(raw, ChatbotMessage):
            hint_task = asyncio.create_task(
                self._hint_later(raw, message.conversation_id, state)
            )

        answer = ""
        try:
            async for delta in chunks:
                answer += delta
        except Exception:  # noqa: BLE001
            log.exception("钉钉回复生成中断")
        finally:
            state["done"] = True
            if hint_task is not None:
                hint_task.cancel()

        if not answer.strip():
            return ""

        if isinstance(raw, ChatbotMessage):
            await self._safe_reply(raw, answer, markdown=True)
        else:
            await self.send_text_to_chat(message.conversation_id, answer)
        return answer

    async def _hint_later(
        self, incoming: ChatbotMessage, chat_id: str, state: dict
    ) -> None:
        try:
            await asyncio.sleep(PROCESSING_HINT_DELAY)
        except asyncio.CancelledError:
            return
        if state.get("done"):
            return
        log.info("钉钉回答超过 %.0f 秒，先回处理中提示", PROCESSING_HINT_DELAY)
        await self._safe_reply(incoming, "正在处理，请稍候…", markdown=False)

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        """钉钉机器人没有 typing 接口，保持无操作。"""
        return None

    async def send_text_to_chat(self, chat_id: str, text: str) -> None:
        """按 userId 主动推送 Markdown 文本。"""
        if not chat_id:
            raise RuntimeError("钉钉发送失败：缺少 userId")
        await asyncio.to_thread(
            dingtalk_api.send_bot_message, [chat_id], text, markdown=True
        )

    async def send_card_to_chat(self, chat_id: str, card: dict) -> str:
        """按卡片描述发送：form=模板表单卡，buttons/markdown=AI Markdown 卡。"""
        if not chat_id:
            raise RuntimeError("钉钉发送失败：缺少 userId")
        if not isinstance(card, dict):
            raise ValueError("钉钉卡片描述必须是字典")

        kind = str(card.get("kind") or "buttons").strip().lower()
        if kind == "form":
            return await asyncio.to_thread(
                dingtalk_api.send_form_card,
                chat_id,
                str(card.get("title") or ""),
                list(card.get("fields") or []),
                dict(card.get("hidden") or {}),
                str(card.get("btn_text") or "提交"),
                str(card.get("template_id") or ""),
            )
        if kind in ("buttons", "markdown"):
            return await self._send_button_card(chat_id, card)
        raise ValueError(f"未知的钉钉卡片类型：{kind!r}")

    async def _send_button_card(self, user_id: str, card: dict) -> str:
        """发 AI Markdown 卡（可带按钮）；返回 outTrackId。"""
        if self._client is None:
            raise RuntimeError("钉钉通道尚未启动，无法发送按钮卡片")

        buttons: list[dict] = []
        for item in card.get("buttons") or []:
            if not isinstance(item, dict):
                continue
            button_id = str(item.get("id") or "").strip()
            if not button_id:
                raise ValueError("钉钉按钮卡缺少按钮 id")
            buttons.append(
                {
                    "text": str(item.get("text") or "操作"),
                    "color": str(item.get("color") or "blue"),
                    "id": button_id,
                    "request": True,
                }
            )

        instance = _ObservableAiCard(self._client, _card_message_shell(user_id))
        instance.set_title_and_logo(str(card.get("title") or "任务卡片"), "")
        await asyncio.to_thread(instance.ai_start)
        if not instance.card_instance_id:
            raise RuntimeError("钉钉按钮卡片创建失败")

        markdown = str(card.get("markdown") or "")
        # 正文只由流式通道渲染：先推一次全量并 finalize，再切完成态，
        # 否则卡片只有标题和按钮、正文为空。
        if markdown.strip():
            await asyncio.to_thread(instance.ai_streaming_final, markdown)
            if not getattr(instance, "last_stream_ok", True):
                raise RuntimeError("钉钉按钮卡片正文推送失败")

        await asyncio.to_thread(instance.ai_finish, markdown, buttons)
        if not getattr(instance, "last_update_ok", True):
            raise RuntimeError("钉钉按钮卡片内容更新失败")

        log.info(
            "钉钉按钮卡已发送 user=%s outTrackId=%s buttons=%d",
            user_id,
            instance.card_instance_id,
            len(buttons),
        )
        return str(instance.card_instance_id)
