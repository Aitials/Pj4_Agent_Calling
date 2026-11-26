"""飞书企业自建应用机器人通道（长连接 WebSocket）。

行为对齐现有 Channel 契约：长连接收消息、去重、媒体落盘，
优先用流式卡片回复文本，失败时降级为普通文本消息。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackCard,
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from lark_oapi.api.cardkit.v1 import (
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from app.channels import (
    register_channel,
    reset_current_message,
    set_current_message,
)
from app.channels.base import Channel, IncomingMessage, MediaAttachment, MessageHandler
from app.core.config import (
    ALLOW_GROUP_CHAT,
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_MEDIA_DIR,
)

log = logging.getLogger("feishu")

STREAM_INTERVAL_SECONDS = 0.5
MAX_SEEN_IDS = 500
CARD_ELEMENT_ID = "answer"
MAX_CARD_CHARS = 100_000
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# 临时调试指令：发送 /cardtest 触发一张按钮测试卡片，用于验证卡片回调通路
CARD_TEST_COMMAND = "/cardtest"
# 任务表单调试入口：验证卡片流程时不依赖模型
CREATE_FORM_COMMAND = "/createform"
START_FORM_COMMAND = "/startform"

_MEDIA_KINDS = {
    "image": "image",
    "file": "file",
    "audio": "voice",
    "media": "video",
}
_DEFAULT_SUFFIX = {
    "image": ".jpg",
    "voice": ".opus",
    "video": ".mp4",
    "file": ".bin",
}

def _safe_name(name: str) -> str:
    """把文件名压成安全的单段名称。"""
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return name[:80]


def _clean_text(text: str) -> str:
    """去掉群聊 @ 标记，保留正文。"""
    text = re.sub(r"@_user_\d+", "", text)
    text = re.sub(r"<at[^>]*>.*?</at>", "", text, flags=re.DOTALL)
    return text.strip()


def _plain_text(text: str) -> str:
    """飞书普通文本不渲染 Markdown，发送前做最小降级。"""
    text = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda m: m.group(1).strip(), text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def _post_text(content: dict) -> str:
    """从飞书富文本 post 中提取纯文本。"""
    section = content.get("zh_cn") or content.get("en_us") or {}
    if not isinstance(section, dict):
        return ""
    lines: list[str] = []
    title = str(section.get("title") or "").strip()
    if title:
        lines.append(title)
    for paragraph in section.get("content") or []:
        if not isinstance(paragraph, list):
            continue
        parts: list[str] = []
        for node in paragraph:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag in ("text", "a", "code_block", "md"):
                parts.append(str(node.get("text") or ""))
        lines.append("".join(parts))
    return "\n".join(lines).strip()


def _post_image_keys(content: dict) -> list[str]:
    """从飞书富文本 post 中提取图片 key。"""
    section = content.get("zh_cn") or content.get("en_us") or {}
    if not isinstance(section, dict):
        return []
    keys: list[str] = []
    for paragraph in section.get("content") or []:
        if not isinstance(paragraph, list):
            continue
        for node in paragraph:
            if isinstance(node, dict) and node.get("tag") == "img":
                key = str(node.get("image_key") or "")
                if key:
                    keys.append(key)
    return keys


def _stream_card_json() -> str:
    """卡片 JSON 2.0：开启流式更新，markdown 元素用于逐字上屏。"""
    card = {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "回答"}},
        "config": {
            "streaming_mode": True,
            "summary": {"content": "正在生成..."},
            "streaming_config": {
                "print_frequency_ms": {
                    "default": 70,
                    "android": 70,
                    "ios": 70,
                    "pc": 70,
                },
                "print_step": {
                    "default": 1,
                    "android": 1,
                    "ios": 1,
                    "pc": 1,
                },
                "print_strategy": "fast",
            },
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "正在生成回答...",
                    "element_id": CARD_ELEMENT_ID,
                }
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def _processing_card(text: str) -> dict:
    """卡片回调响应里的「处理中」卡：置灰并去掉按钮，避免继续连点。"""
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "处理中"},
            "template": "grey",
        },
        "body": {"elements": [{"tag": "markdown", "content": text}]},
    }


class FeishuChannel(Channel):
    """飞书长连接通道。"""

    name = "feishu"
    supports_cards = True

    def __init__(self) -> None:
        if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
            raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先在 .env 中配置")

        self.media_dir = FEISHU_MEDIA_DIR
        self._client = (
            lark.Client.builder()
            .app_id(FEISHU_APP_ID)
            .app_secret(FEISHU_APP_SECRET)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._queue: asyncio.Queue[P2ImMessageReceiveV1] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seen: deque[str] = deque()
        self._seen_set: set[str] = set()
        self._ws_thread: threading.Thread | None = None
        # 任务表单卡片回调处理，由 router 注入
        self._form_handler: Any = None
        register_channel(self)

    # ------------------------------------------------------------------ 启动

    async def start(self, on_message: MessageHandler) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_event)
            .register_p2_card_action_trigger(self._on_card_action)
            .build()
        )
        ws = lark.ws.Client(
            FEISHU_APP_ID,
            FEISHU_APP_SECRET,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        self._ws_thread = threading.Thread(
            target=self._run_ws, args=(ws,), name="feishu-ws", daemon=True
        )
        self._ws_thread.start()
        log.info("飞书长连接已启动，等待消息")

        while True:
            event = await self._queue.get()
            try:
                message = await self._to_incoming(event)
            except Exception:
                log.exception("飞书消息解析失败，已跳过")
                continue
            if not message:
                continue
            handle = message.reply_handle if isinstance(message.reply_handle, dict) else {}
            if handle.get("unsupported"):
                try:
                    await self.reply_text(message, "目前仅支持文字消息，请用文字描述。")
                except Exception:
                    log.exception("发送不支持消息类型提示失败")
                continue
            # 临时调试：/cardtest 发一张按钮卡片，用于验证「发卡 → 点按钮 → 收回调」通路
            if message.text.strip().lower() == CARD_TEST_COMMAND:
                try:
                    await self.send_test_card(message)
                except Exception:
                    log.exception("发送测试卡片失败")
                continue
            # 任务表单调试入口：不经过模型，直接发卡
            command = message.text.strip().lower()
            if command in (CREATE_FORM_COMMAND, START_FORM_COMMAND):
                try:
                    await self._open_task_form(command, message)
                except Exception:
                    log.exception("发送任务表单失败")
                continue
            token = set_current_message(message)
            try:
                await on_message(message)
            finally:
                reset_current_message(token)

    def _run_ws(self, ws: Any) -> None:
        try:
            ws.start()
        except Exception:
            log.exception("飞书长连接异常退出")

    def _on_event(self, data: P2ImMessageReceiveV1) -> None:
        """事件回调只做去重和入队，必须在 3 秒内返回。"""
        try:
            event = data.event
            if not event or not event.message or not event.sender:
                return
            sender = event.sender
            if sender.sender_type and sender.sender_type != "user":
                return
            message_id = str(event.message.message_id or "")
            if not message_id or message_id in self._seen_set:
                return
            self._remember(message_id)
            if self._loop and self._queue:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except Exception:
            log.exception("飞书事件解析失败")

    def _remember(self, key: str) -> None:
        self._seen.append(key)
        self._seen_set.add(key)
        while len(self._seen) > MAX_SEEN_IDS:
            old = self._seen.popleft()
            self._seen_set.discard(old)

    # ------------------------------------------------------------ 卡片回调（最小闭环）

    async def send_test_card(self, message: IncomingMessage) -> None:
        """发一张带「确认 / 取消」按钮的静态卡片，点按钮只会回 toast 并原地更新。"""
        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "操作确认（回调通路测试）"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            "**任务名称**：回调测试任务\n"
                            "**话术 ID**：66286\n"
                            "**主叫线路**：057128215420（台铃测试）\n"
                            "**并发 / BOT**：1 / 1\n\n"
                            "这是一张测试卡片，点按钮不会创建或启动任何任务。"
                        ),
                    },
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "horizontal_spacing": "default",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "button",
                                        "text": {"tag": "plain_text", "content": "取消"},
                                        "type": "default",
                                        "width": "fill",
                                        "behaviors": [
                                            {
                                                "type": "callback",
                                                "value": {"action": "cancel", "scene": "card_test"},
                                            }
                                        ],
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "button",
                                        "text": {"tag": "plain_text", "content": "确认创建"},
                                        "type": "primary",
                                        "width": "fill",
                                        "behaviors": [
                                            {
                                                "type": "callback",
                                                "value": {"action": "confirm", "scene": "card_test"},
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                ]
            },
        }
        content = json.dumps(card, ensure_ascii=False)
        await self._send_content(message, "interactive", content)
        log.info("测试卡片已发送 chat=%s", self._chat_id(message))

    def _on_card_action(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        """卡片按钮回调：3 秒内必须返回，这里只回 toast 并替换卡片内容。"""
        response = P2CardActionTriggerResponse()
        try:
            event = data.event
            if not event or not event.action:
                response.toast = CallBackToast({"type": "error", "content": "回调数据为空"})
                return response

            value = event.action.value or {}
            action = str(value.get("action") or "")
            scene = str(value.get("scene") or "")
            operator = event.operator.open_id if event.operator else ""
            context = event.context
            log.info(
                "收到卡片回调 scene=%s action=%s operator=%s chat=%s message=%s value=%s",
                scene,
                action,
                operator,
                getattr(context, "open_chat_id", ""),
                getattr(context, "open_message_id", ""),
                value,
            )

            form_value = getattr(event.action, "form_value", None) or {}
            if action.startswith("task_"):
                return self._handle_task_action(
                    action, value, form_value, operator, context, response
                )

            if action == "confirm":
                response.toast = CallBackToast({"type": "success", "content": "已收到确认（测试）"})
                title, template = "已确认（测试）", "green"
            elif action == "cancel":
                response.toast = CallBackToast({"type": "info", "content": "已取消（测试）"})
                title, template = "已取消（测试）", "grey"
            else:
                response.toast = CallBackToast({"type": "error", "content": "未知操作"})
                title, template = "未知操作", "red"

            result_card = {
                "schema": "2.0",
                "header": {"title": {"tag": "plain_text", "content": title}, "template": template},
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"**场景**：{scene or '-'}\n"
                                f"**动作**：{action or '-'}\n"
                                f"**操作人**：{operator or '-'}\n\n"
                                "回调通路正常，正式确认逻辑将在下一步接入。"
                            ),
                        }
                    ]
                },
            }
            response.card = CallBackCard({"type": "raw", "data": result_card})
            return response
        except Exception:
            log.exception("处理卡片回调失败")
            response.toast = CallBackToast({"type": "error", "content": "处理回调失败，请查看日志"})
            return response

    def _handle_task_action(
        self,
        action: str,
        value: dict,
        form_value: dict,
        operator: str,
        context: Any,
        response: P2CardActionTriggerResponse,
    ) -> P2CardActionTriggerResponse:
        """任务表单/确认按钮：3 秒内回 toast，真正的处理放到事件循环异步做。"""
        # 取消是同步操作：作废 token 并把卡片原地关掉，避免用户以为还在流程里
        if action == "task_form_cancel":
            token = str(value.get("token") or "")
            if token:
                from app.core import forms

                forms.pop_for(token, operator)
            log.info("用户取消任务卡片流程 token=%s operator=%s", token, operator)
            response.toast = CallBackToast({"type": "info", "content": "已取消"})
            response.card = CallBackCard(
                {
                    "type": "raw",
                    "data": {
                        "schema": "2.0",
                        "header": {
                            "title": {"tag": "plain_text", "content": "已取消"},
                            "template": "grey",
                        },
                        "body": {
                            "elements": [
                                {"tag": "markdown", "content": "本次操作已取消，不会提交到平台。"}
                            ]
                        },
                    },
                }
            )
            return response

        if self._form_handler is None or self._loop is None:
            response.toast = CallBackToast({"type": "error", "content": "服务尚未就绪，请稍后重试"})
            return response

        chat_id = str(getattr(context, "open_chat_id", "") or "")
        message_id = str(getattr(context, "open_message_id", "") or "")

        asyncio.run_coroutine_threadsafe(
            self._form_handler(
                action, value, form_value, operator, chat_id, message_id
            ),
            self._loop,
        )

        # 通道层只做「视觉失效」：真正的 first-wins 由业务层
        # forms.consume_card_action 统一处理。这里不再消费同一个闸门，
        # 否则正常点击会被二次消费挡掉。
        if action in ("task_status_pick", "task_status_refresh"):
            response.toast = CallBackToast(
                {"type": "success", "content": "已提交，正在读取任务概况"}
            )
            response.card = CallBackCard(
                {"type": "raw", "data": _processing_card("已提交，正在读取任务概况…")}
            )
            return response

        if action.endswith("_confirm") or action.endswith("_step1") or action.endswith("_step2"):
            response.toast = CallBackToast({"type": "success", "content": "已提交，正在处理"})
            response.card = CallBackCard(
                {"type": "raw", "data": _processing_card("已提交，请稍候…")}
            )
        else:
            response.toast = CallBackToast({"type": "info", "content": "正在刷新…"})
        return response

    # ------------------------------------------------------------------ 收消息

    async def _to_incoming(self, data: P2ImMessageReceiveV1) -> IncomingMessage | None:
        event = data.event
        if not event or not event.message or not event.sender:
            return None
        message = event.message
        sender = event.sender

        sender_id = sender.sender_id
        user_id = ""
        if sender_id:
            user_id = str(
                sender_id.open_id or sender_id.user_id or sender_id.union_id or ""
            )
        if not user_id:
            return None

        chat_type = str(message.chat_type or "")
        if chat_type and chat_type != "p2p" and not ALLOW_GROUP_CHAT:
            log.info(
                "群聊已关闭，忽略消息 chat_id=%s chat_type=%s",
                message.chat_id, chat_type,
            )
            return None

        chat_id = str(message.chat_id or "")
        message_type = str(message.message_type or "")
        message_id = str(message.message_id or "")
        reply_handle = {
            "chat_id": chat_id,
            "message_id": message_id,
            "chat_type": chat_type,
            "message_type": message_type,
        }

        # 当前只支持纯文字：其他类型不再下载媒体，交由队列循环统一提示
        if message_type != "text":
            return IncomingMessage(
                channel=self.name,
                message_id=message_id,
                user_id=user_id,
                conversation_id=user_id,
                text="",
                raw=message,
                reply_handle={**reply_handle, "unsupported": True},
            )

        try:
            content = json.loads(message.content or "{}")
        except json.JSONDecodeError:
            log.warning("飞书消息 content 不是合法 JSON：%s", message.content)
            return None
        if not isinstance(content, dict):
            return None

        text = _clean_text(str(content.get("text") or "")).strip()
        if not text:
            return None

        return IncomingMessage(
            channel=self.name,
            message_id=message_id,
            user_id=user_id,
            conversation_id=user_id,
            text=text,
            raw=message,
            reply_handle=reply_handle,
        )

    async def _download_media(
        self,
        message_id: str,
        key: str,
        kind: str,
        resource_type: str,
        file_name: str,
    ) -> MediaAttachment | None:
        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(key)
                .type(resource_type)
                .build()
            )
            response = await asyncio.to_thread(
                self._client.im.v1.message_resource.get, request
            )
            if not response.success():
                raise RuntimeError(
                    f"下载资源失败：code={response.code} msg={response.msg}"
                )
            if not response.file:
                raise RuntimeError("下载资源失败：响应没有文件内容")
            content = response.file.read()
            name = file_name or response.file_name or ""
            if kind == "image" and not Path(name).suffix:
                content_type = ""
                raw = getattr(response, "raw", None)
                if raw is not None and getattr(raw, "headers", None):
                    content_type = str(raw.headers.get("Content-Type") or "")
                suffix = mimetypes.guess_extension(content_type.split(";")[0]) or ""
                name = f"image{suffix}" if suffix else name
            saved = self._save_media(content, kind, name)
            return MediaAttachment(
                kind=kind,
                path=str(saved),
                file_name=file_name or response.file_name or "",
            )
        except Exception as exc:
            log.warning("飞书媒体下载失败（%s）：%s", kind, exc)
            return MediaAttachment(kind=kind, path="", file_name=file_name)

    def _save_media(self, content: bytes, kind: str, file_name: str) -> Path:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_name).suffix or _DEFAULT_SUFFIX.get(kind, ".bin")
        safe = _safe_name(file_name) or f"feishu_{kind}{suffix}"
        if not Path(safe).suffix:
            safe += suffix
        filename = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{safe}"
        path = self.media_dir / filename
        path.write_bytes(content)
        return path

    # ------------------------------------------------------------------ 回复

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        content = json.dumps(
            {"text": _plain_text(text) or "（没有生成内容）"}, ensure_ascii=False
        )
        await self._send_content(message, "text", content)

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        answer = ""
        card_id: str | None = None
        card_attempted = False
        sequence = 1
        last_push = 0.0
        try:
            async for delta in chunks:
                answer += delta
                # 懒创建：本轮没有文本输出时（例如只发了卡片）就不发空卡片
                if card_id is None and not card_attempted:
                    card_attempted = True
                    card_id = await self._create_and_send_card(message)
                now = time.monotonic()
                if card_id and now - last_push >= STREAM_INTERVAL_SECONDS:
                    sequence += 1
                    if not await self._update_card(card_id, answer, sequence):
                        card_id = None
                    last_push = now
        except Exception:
            log.exception("飞书流式回复失败")
            answer = answer or "抱歉，处理失败了，请稍后再试。"

        if card_id:
            sequence += 1
            updated = await self._update_card(
                card_id, answer or "（没有生成内容）", sequence
            )
            sequence += 1
            await self._close_card(card_id, sequence)
            if not updated:
                await self.reply_text(message, answer or "（没有生成内容）")
            return answer

        if answer:
            await self.reply_text(message, answer)
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        # 飞书机器人没有官方 typing 接口，保持无操作。
        return None

    # ------------------------------------------------------------ 任务表单卡片

    def set_form_handler(self, handler: Any) -> None:
        """注入任务卡片流程处理函数（router 负责绑定 channel）。"""
        self._form_handler = handler

    async def _open_task_form(self, command: str, message: IncomingMessage) -> None:
        if self._form_handler is None:
            await self.reply_text(message, "任务表单流程尚未就绪，请稍后重试。")
            return
        action = (
            "debug_open_create"
            if command == CREATE_FORM_COMMAND
            else "debug_open_start"
        )
        await self._form_handler(
            action, {}, {}, message.user_id, self._chat_id(message), message.message_id
        )

    async def send_card_to_chat(self, chat_id: str, card: dict) -> None:
        """按 chat_id 发送一张卡片（卡片 JSON 对象）。"""
        if not chat_id:
            raise RuntimeError("飞书发送失败：缺少 chat_id")
        content = json.dumps(card, ensure_ascii=False)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(content)
                .build()
            )
            .build()
        )
        response = await asyncio.to_thread(self._client.im.v1.message.create, request)
        self._ensure_success(response, "发送卡片")

    async def send_text_to_chat(self, chat_id: str, text: str) -> None:
        """按 chat_id 主动发一条文本（用于恢复执行后的结果回传）。"""
        if not chat_id:
            raise RuntimeError("飞书发送失败：缺少 chat_id")
        content = json.dumps({"text": _plain_text(text) or "（没有生成内容）"}, ensure_ascii=False)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )
        response = await asyncio.to_thread(self._client.im.v1.message.create, request)
        self._ensure_success(response, "发送消息")

    async def _create_and_send_card(self, message: IncomingMessage) -> str | None:
        try:
            request = (
                CreateCardRequest.builder()
                .request_body(
                    CreateCardRequestBody.builder()
                    .type("card_json")
                    .data(_stream_card_json())
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card.create, request
            )
            self._ensure_success(response, "创建卡片")
            card_id = str(response.data.card_id)
            content = json.dumps(
                {"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False
            )
            await self._send_content(message, "interactive", content)
            return card_id
        except Exception:
            log.exception("飞书流式卡片创建失败，降级为文本回复")
            return None

    async def _update_card(self, card_id: str, content: str, sequence: int) -> bool:
        try:
            request = (
                ContentCardElementRequest.builder()
                .card_id(card_id)
                .element_id(CARD_ELEMENT_ID)
                .request_body(
                    ContentCardElementRequestBody.builder()
                    .content(content[:MAX_CARD_CHARS])
                    .sequence(sequence)
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card_element.content, request
            )
            if not response.success():
                log.warning(
                    "飞书卡片更新失败：code=%s msg=%s", response.code, response.msg
                )
                return False
            return True
        except Exception:
            log.exception("飞书卡片更新异常")
            return False

    async def _close_card(self, card_id: str, sequence: int) -> bool:
        try:
            settings = json.dumps(
                {"config": {"streaming_mode": False}}, ensure_ascii=False
            )
            request = (
                SettingsCardRequest.builder()
                .card_id(card_id)
                .request_body(
                    SettingsCardRequestBody.builder()
                    .settings(settings)
                    .sequence(sequence)
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card.settings, request
            )
            if not response.success():
                log.warning(
                    "飞书关闭流式模式失败：code=%s msg=%s", response.code, response.msg
                )
                return False
            return True
        except Exception:
            log.exception("飞书关闭流式模式异常")
            return False

    async def _send_content(
        self, message: IncomingMessage, msg_type: str, content: str
    ) -> None:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        request = (
            ReplyMessageRequest.builder()
            .message_id(message.message_id)
            .request_body(body)
            .build()
        )
        try:
            response = await asyncio.to_thread(
                self._client.im.v1.message.reply, request
            )
            if response.success():
                return
            log.warning(
                "飞书回复消息失败：code=%s msg=%s，尝试直接发送",
                response.code,
                response.msg,
            )
        except Exception:
            log.exception("飞书回复消息异常，尝试直接发送")

        chat_id = self._chat_id(message)
        if not chat_id:
            raise RuntimeError("飞书发送失败：缺少 chat_id")
        create_request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = await asyncio.to_thread(
            self._client.im.v1.message.create, create_request
        )
        self._ensure_success(response, "发送消息")

    def _chat_id(self, message: IncomingMessage) -> str:
        if isinstance(message.reply_handle, dict):
            chat_id = str(message.reply_handle.get("chat_id") or "")
            if chat_id:
                return chat_id
        return str(getattr(message.raw, "chat_id", "") or "")

    @staticmethod
    def _ensure_success(response: Any, action: str) -> None:
        if not response.success():
            raise RuntimeError(
                f"{action}失败：code={response.code} msg={response.msg}"
            )

    # ------------------------------------------------------------------ 发媒体

    async def send_file(
        self,
        message: IncomingMessage,
        path: str,
        kind: str = "file",
        file_name: str = "",
    ) -> None:
        """向当前飞书会话发送图片、文件、视频或语音。"""
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"文件不存在：{file_path}")
        size = file_path.stat().st_size
        name = file_name or file_path.name
        normalized = kind.strip().lower()

        if normalized == "image":
            if size > MAX_IMAGE_BYTES:
                raise ValueError("飞书图片上限 10 MB")
            image_key = await self._upload_image(file_path)
            content = json.dumps({"image_key": image_key}, ensure_ascii=False)
            await self._send_content(message, "image", content)
            return

        if normalized == "video":
            if file_path.suffix.lower() != ".mp4":
                raise ValueError("飞书视频消息仅支持 mp4")
            if size > MAX_FILE_BYTES:
                raise ValueError("飞书 IM 文件上限 30 MB，大视频请改为发送链接")
            file_key = await self._upload_file(file_path, "mp4", name)
            content = json.dumps(
                {"file_key": file_key, "file_name": name}, ensure_ascii=False
            )
            await self._send_content(message, "media", content)
            return

        if normalized in ("voice", "audio"):
            if file_path.suffix.lower() != ".opus":
                raise ValueError("飞书语音消息仅支持 opus")
            if size > MAX_FILE_BYTES:
                raise ValueError("飞书 IM 文件上限 30 MB")
            file_key = await self._upload_file(file_path, "opus", name)
            content = json.dumps(
                {"file_key": file_key, "duration": 0}, ensure_ascii=False
            )
            await self._send_content(message, "audio", content)
            return

        if size > MAX_FILE_BYTES:
            raise ValueError("飞书 IM 文件上限 30 MB，请改为发送链接")
        file_key = await self._upload_file(file_path, "stream", name)
        content = json.dumps({"file_key": file_key}, ensure_ascii=False)
        await self._send_content(message, "file", content)

    async def _upload_file(self, path: Path, file_type: str, file_name: str) -> str:
        with path.open("rb") as file_obj:
            body = (
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(file_name)
                .file(file_obj)
                .build()
            )
            request = CreateFileRequest.builder().request_body(body).build()
            response = await asyncio.to_thread(
                self._client.im.v1.file.create, request
            )
        self._ensure_success(response, "上传文件")
        if not response.data or not response.data.file_key:
            raise RuntimeError("上传文件失败：响应缺少 file_key")
        return str(response.data.file_key)

    async def _upload_image(self, path: Path) -> str:
        with path.open("rb") as file_obj:
            body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(file_obj)
                .build()
            )
            request = CreateImageRequest.builder().request_body(body).build()
            response = await asyncio.to_thread(
                self._client.im.v1.image.create, request
            )
        self._ensure_success(response, "上传图片")
        if not response.data or not response.data.image_key:
            raise RuntimeError("上传图片失败：响应缺少 image_key")
        return str(response.data.image_key)
