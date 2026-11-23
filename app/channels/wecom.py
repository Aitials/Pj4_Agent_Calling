"""企业微信智能机器人通道（当前阶段：文本对话 + 流式输出）。

长连接模式下，收到 aibot_msg_callback 后用同一帧的 req_id 回复；
流式回复通过同一个 stream_id 反复推送，最后以 finish=True 收尾。
飞书通道保持独立，两者互不影响。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from aibot import WSClient, generate_req_id

from app.channels.base import Channel, IncomingMessage, MessageHandler
from app.clients import wecom_api

log = logging.getLogger("wecom")

STREAM_INTERVAL_SECONDS = 0.6
MAX_SEEN_IDS = 500
_SEEN_TTL_SECONDS = 600.0


def _frame_body(frame: dict) -> dict:
    body = frame.get("body")
    return body if isinstance(body, dict) else {}


def _sender_userid(body: dict) -> str:
    sender = body.get("from")
    if isinstance(sender, dict):
        return str(sender.get("userid") or sender.get("user_id") or "")
    return ""


class WecomChannel(Channel):
    name = "wecom"

    def __init__(self) -> None:
        self._client: WSClient | None = None
        self._handler: MessageHandler | None = None
        self._seen: dict[str, float] = {}

    # ------------------------------------------------------------------ 启动

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        client = wecom_api.build_ws_client()
        self._client = client

        client.on("authenticated", lambda: log.info("企微长连接认证成功"))
        client.on("disconnected", lambda reason: log.warning("企微长连接断开：%s", reason))
        client.on("error", lambda exc: log.error("企微长连接异常：%s", exc))
        client.on("message", self._on_message_frame)

        await client.connect()
        log.info("企微长连接已启动，等待消息")
        await asyncio.Event().wait()

    # ------------------------------------------------------------------ 收消息

    async def _on_message_frame(self, frame: dict) -> None:
        try:
            body = _frame_body(frame)
            msgtype = str(body.get("msgtype") or "")
            msgid = str(
                body.get("msgid") or (frame.get("headers") or {}).get("req_id") or ""
            )
            if msgid and self._is_duplicate(msgid):
                log.info("重复消息已忽略 msgid=%s", msgid)
                return

            user_id = _sender_userid(body)
            if not user_id:
                log.warning("企微消息缺少发送人，已忽略 frame=%s", str(frame)[:300])
                return

            text, prompt = self._extract_text(body, msgtype)
            if prompt:
                await self._reply_prompt(frame, body, prompt)
                return
            if not text:
                return

            message = IncomingMessage(
                channel=self.name,
                message_id=msgid,
                user_id=user_id,
                conversation_id=str(body.get("chatid") or user_id),
                text=text,
                raw=frame,
                reply_handle=frame,
            )
            if self._handler is not None:
                await self._handler(message)
        except Exception:  # noqa: BLE001
            log.exception("处理企微消息失败")

    @staticmethod
    def _extract_text(body: dict, msgtype: str) -> tuple[str, str]:
        """返回 (文本, 提示语)；提示语非空表示该消息不进入 Agent。"""
        if msgtype == "text":
            return str((body.get("text") or {}).get("content") or "").strip(), ""
        if msgtype == "voice":
            # 企微智能机器人已在 voice.content 中给出转写文本
            content = str((body.get("voice") or {}).get("content") or "").strip()
            if content:
                return content, ""
            return "", "没有识别到语音内容，请重发或改用文字。"
        if msgtype in ("image", "file", "video", "mixed"):
            return "", "暂不支持图片/文件消息，请发送文字。"
        if not msgtype:
            return "", ""
        return "", "暂不支持该类型消息，请发送文字。"

    async def _reply_prompt(self, frame: dict, body: dict, text: str) -> None:
        """用同一帧回一条提示；失败时降级为主动推送。"""
        client = self._client
        if client is None:
            return
        try:
            await client.reply_stream(frame, generate_req_id("stream"), text, True)
            return
        except Exception:  # noqa: BLE001
            log.exception("企微提示回复失败，尝试主动推送")

        chat_id = str(body.get("chatid") or _sender_userid(body))
        if not chat_id:
            return
        try:
            await self.send_text_to_chat(chat_id, text)
        except Exception:  # noqa: BLE001
            log.exception("企微主动推送提示也失败")

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

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        frame = message.reply_handle
        client = self._client
        if not isinstance(frame, dict) or client is None:
            await self.send_text_to_chat(message.conversation_id, text)
            return
        await client.reply_stream(
            frame,
            generate_req_id("stream"),
            text or "（没有生成内容）",
            True,
        )

    async def reply_stream(
        self, message: IncomingMessage, chunks: AsyncIterator[str]
    ) -> str:
        answer = ""
        frame = message.reply_handle
        client = self._client

        # 通道尚未就绪时退化为收集后主动推送
        if not isinstance(frame, dict) or client is None:
            async for delta in chunks:
                answer += delta
            if answer:
                await self.send_text_to_chat(message.conversation_id, answer)
            return answer

        stream_id = generate_req_id("stream")
        last_push = 0.0
        pushed = False
        try:
            async for delta in chunks:
                answer += delta
                now = time.monotonic()
                # 首包立刻发，避免超过企微 5 秒首响窗口；之后节流推送
                if not pushed or now - last_push >= STREAM_INTERVAL_SECONDS:
                    await client.reply_stream(frame, stream_id, answer, False)
                    pushed = True
                    last_push = now
        except Exception:  # noqa: BLE001
            log.exception("企微流式回复中断")

        if not answer:
            return ""

        try:
            await client.reply_stream(frame, stream_id, answer, True)
        except Exception:  # noqa: BLE001
            log.exception("企微流式回复收尾失败，降级为普通消息")
            await self.send_text_to_chat(message.conversation_id, answer)
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        """企微智能机器人没有 typing 接口，保持无操作。"""
        return None

    async def send_text_to_chat(self, chat_id: str, text: str) -> None:
        """按会话 ID 主动推送 Markdown 文本。"""
        if self._client is None:
            raise RuntimeError("企微通道尚未启动")
        if not chat_id:
            raise RuntimeError("企微发送失败：缺少 chat_id")
        await self._client.send_message(
            chat_id,
            {"msgtype": "markdown", "markdown": {"content": text or "（空消息）"}},
        )

    async def send_card_to_chat(self, chat_id: str, card: dict) -> None:
        """按会话 ID 主动发送模板卡片（企微 template_card）。"""
        if self._client is None:
            raise RuntimeError("企微通道尚未启动")
        if not chat_id:
            raise RuntimeError("企微发送卡片失败：缺少 chat_id")
        if not isinstance(card, dict) or not card.get("card_type"):
            raise ValueError("企微模板卡片缺少 card_type")

        ack = await self._client.send_message(
            chat_id, {"msgtype": "template_card", "template_card": card}
        )
        errcode = ack.get("errcode") if isinstance(ack, dict) else None
        if errcode not in (0, None):
            raise RuntimeError(
                f"企微发送卡片失败：errcode={errcode} errmsg={ack.get('errmsg') or ''}"
            )
