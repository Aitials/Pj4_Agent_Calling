"""钉钉企业内部应用机器人通道（Stream 模式，阶段 1：文本对话）。

Stream 模式免公网回调、免 AES 加解密：SDK 主动连钉钉网关，
订阅机器人消息 topic `/v1.0/im/bot/messages/get`。
回调里的 senderStaffId 就是明文 userId，会话记忆直接按它隔离。
钉钉机器人没有逐字推送机制，流式回答降级为「处理中提示 + 完成后整段结果」。

注意：事件订阅（EVENT）与机器人消息（CALLBACK）是两条独立通道。
后台「事件订阅 → Stream 模式推送 → 验证连接通道」校验的是 EVENT 通道，
因此除 chatbot handler 外必须再注册 EventHandler，否则验证失败、无法保存推送方式。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

import dingtalk_stream  # noqa: F401  SDK 日志/类型来源
import httpx
from dingtalk_stream import (
    AckMessage,
    CallbackMessage,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
    EventHandler,
    EventMessage,
)

from app.channels.base import Channel, IncomingMessage, MessageHandler
from app.clients import dingtalk_api
from app.core.config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET

log = logging.getLogger("dingtalk")

# 回答超过该时长仍未完成时，先回一条「处理中」
PROCESSING_HINT_DELAY = 4.0
MAX_SEEN_IDS = 500
_SEEN_TTL_SECONDS = 600.0
_WEBHOOK_TIMEOUT_SECONDS = 5.0


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


class DingtalkChannel(Channel):
    name = "dingtalk"

    def __init__(self) -> None:
        self._client: DingTalkStreamClient | None = None
        self._handler: MessageHandler | None = None
        self._seen: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 启动

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        if not dingtalk_api.is_configured():
            raise RuntimeError(
                "缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请在 .env 中配置"
            )

        client = DingTalkStreamClient(
            Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
            logger=logging.getLogger("dingtalk_stream"),
        )
        client.register_callback_handler(ChatbotMessage.TOPIC, _BotHandler(self))
        # 关键：注册事件订阅 handler，使建连时的 subscriptions 带上 EVENT: *，
        # 后台「验证连接通道」才能通过，事件订阅推送方式才能保存。
        client.register_all_event_handler(_EventHandler())
        self._client = client

        log.info(
            "钉钉 Stream 客户端已启动，订阅 CALLBACK topic=%s + EVENT:*",
            ChatbotMessage.TOPIC,
        )
        # SDK 内部自带断线重连，异常不会向外抛
        await client.start()

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
        task = asyncio.create_task(self._handler(message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
        """钉钉不支持逐字上屏：先挂「处理中」，完成后整段发结果。"""
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

    async def send_card_to_chat(self, chat_id: str, card: dict) -> None:
        raise NotImplementedError("钉钉互动卡片将在阶段 4 接入")
