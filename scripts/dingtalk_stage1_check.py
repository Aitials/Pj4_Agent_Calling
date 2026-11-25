"""钉钉渠道 · 阶段 1 冒烟测试。

覆盖计划书阶段 1 的四项验收：
1. Stream 连接并认证成功（--stream 模式下连接即认证）
2. 单聊发消息 → 收到回调，打印明文 senderStaffId
3. 主动推送单聊消息（oToMessages/batchSend）可达
4. 主动推送群聊消息（groupMessages/send）可达（需先把机器人拉进群）

用法：
    python scripts/dingtalk_stage1_check.py check
    python scripts/dingtalk_stage1_check.py token
    python scripts/dingtalk_stage1_check.py push  --user-id <userId> [--text 文本]
    python scripts/dingtalk_stage1_check.py group --conversation-id <openConversationId> [--text 文本]
    python scripts/dingtalk_stage1_check.py stream [--seconds 120]
    python scripts/dingtalk_stage1_check.py replay [--file 回调.json]

注意：
- 同一个应用只允许一条 Stream 长连接，跑 --stream 前先停掉 run_dingtalk.py；
- 主动推送走 OpenAPI，必须先在开发者后台配置服务端出口 IP 白名单。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as metadata
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import dingtalk_stream  # noqa: E402,F401  SDK 版本自检用
from dingtalk_stream import (  # noqa: E402
    AckMessage,
    CallbackMessage,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
    EventHandler,
    EventMessage,
)

from app.clients import dingtalk_api  # noqa: E402
from app.core.config import (  # noqa: E402
    DINGTALK_AGENT_ID,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
    DINGTALK_CORP_ID,
    DINGTALK_ROBOT_CODE,
)

log = logging.getLogger("dingtalk_check")

DEFAULT_PUSH_TEXT = "【钉钉阶段1冒烟】主动推送测试"


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "未安装"


def cmd_check() -> int:
    print("== 环境 ==")
    print(f"Python           : {sys.version.split()[0]}")
    print(f"dingtalk-stream  : {_version('dingtalk-stream')}")
    print(f"alibabacloud-钉钉 : {_version('alibabacloud-dingtalk')}")
    print(f"消息 topic        : {ChatbotMessage.TOPIC}")
    print("\n== 配置（.env）==")
    print(f"DINGTALK_CLIENT_ID     : {dingtalk_api.mask_secret(DINGTALK_CLIENT_ID) or '（空）'}")
    print(f"DINGTALK_CLIENT_SECRET : {dingtalk_api.mask_secret(DINGTALK_CLIENT_SECRET) or '（空）'}")
    print(f"DINGTALK_CORP_ID       : {DINGTALK_CORP_ID or '（空，本期可不填）'}")
    print(f"DINGTALK_ROBOT_CODE    : {DINGTALK_ROBOT_CODE or '（空，主动推送需要）'}")
    print(f"DINGTALK_AGENT_ID      : {DINGTALK_AGENT_ID or '（空，预留）'}")

    print("\n== 结论 ==")
    if dingtalk_api.is_configured():
        print("凭证已配置，可继续执行 token / push / group / stream")
        return 0
    print("缺少 Client ID / Secret，请先在 .env 中填写后再测")
    return 1


def cmd_token() -> int:
    token = dingtalk_api.access_token()
    if not token:
        print("accessToken 获取失败：检查 Client ID / Secret、应用是否发布、出口 IP 白名单")
        return 1
    print(f"accessToken 获取成功：{dingtalk_api.mask_secret(token)}（有效期约 7200 秒）")
    return 0


def cmd_push(user_id: str, text: str) -> int:
    text = f"{text} {time.strftime('%H:%M:%S')}"
    try:
        dingtalk_api.send_bot_message([user_id], text)
    except Exception as exc:  # noqa: BLE001
        print(f"单聊主动推送失败：{exc}")
        return 1
    print(f"单聊主动推送成功：user={user_id} text={text}")
    return 0


def cmd_group(conversation_id: str, text: str) -> int:
    text = f"{text} {time.strftime('%H:%M:%S')}"
    try:
        dingtalk_api.send_group_message(conversation_id, text)
    except Exception as exc:  # noqa: BLE001
        print(f"群聊主动推送失败：{exc}")
        return 1
    print(f"群聊主动推送成功：conversation={conversation_id} text={text}")
    return 0


class _FakeCallback:
    """replay 用的回调壳，形状与 SDK 的 CallbackMessage 一致（只用到 .data）。"""

    def __init__(self, data: dict) -> None:
        self.data = data


# 与真实单聊回调同构；senderStaffId 为占位值，不会触达真实用户
SAMPLE_CALLBACK = {
    "senderPlatform": "Win",
    "conversationId": "cid-replay",
    "chatbotCorpId": "corp-replay",
    "msgId": "replay-msg-1",
    "senderNick": "重放测试",
    "senderStaffId": "replay-user",
    "conversationType": "1",
    "senderId": "sender-replay",
    "text": {"content": "重放测试"},
    "robotCode": "robot-replay",
    "msgtype": "text",
}


async def _run_replay(data: dict) -> int:
    """同一回调帧连投两次，验证 msgId 去重（不进 Stream、不发消息）。"""
    from app.channels.dingtalk import DingtalkChannel

    channel = DingtalkChannel()
    received: list = []

    async def handler(message) -> None:
        received.append(message)

    channel._handler = handler  # 冒烟脚本直接注入，避免启动真实 Stream

    await channel.on_callback(_FakeCallback(data))
    await asyncio.sleep(0.05)
    first = len(received)

    await channel.on_callback(_FakeCallback(data))
    await asyncio.sleep(0.05)
    second = len(received)

    print(f"第一次投递：{'进入 Agent' if first == 1 else '未进入 Agent'}（handler 调用 {first} 次）")
    print(f"第二次投递：{'被去重' if second == 1 else '重复进入 Agent'}（累计 {second} 次）")
    return 0 if first == 1 and second == 1 else 1


class _VerifyEventHandler(EventHandler):
    """事件订阅 handler：让客户端上报 EVENT: *，供后台"验证连接通道"通过。"""

    async def process(self, event: EventMessage):
        topic = getattr(getattr(event, "headers", None), "topic", "")
        print(f"\n== 收到事件订阅推送 ==\ntopic={topic}")
        return AckMessage.STATUS_OK, "OK"


class _PrintHandler(ChatbotHandler):
    """打印真实回调帧，便于确认 senderStaffId / conversationType 等字段。"""

    def __init__(self) -> None:
        super().__init__()
        self.received = asyncio.Event()

    async def process(self, callback: CallbackMessage):
        data = callback.data if isinstance(callback.data, dict) else {}
        print("\n== 收到原始回调 ==")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])

        incoming = ChatbotMessage.from_dict(data)
        text = getattr(getattr(incoming, "text", None), "content", "") or ""
        print("\n== 关键字段 ==")
        print(f"msgId            : {incoming.message_id}")
        print(f"senderStaffId    : {incoming.sender_staff_id!r}  ← 会话记忆用的明文 userId")
        print(f"senderId         : {incoming.sender_id!r}")
        print(f"senderNick       : {incoming.sender_nick!r}")
        print(f"conversationType : {incoming.conversation_type!r}  （1=单聊 2=群聊）")
        print(f"conversationId   : {incoming.conversation_id!r}")
        print(f"robotCode        : {incoming.robot_code!r}")
        print(f"msgtype          : {incoming.message_type!r}")
        print(f"text             : {text!r}")
        print(f"sessionWebhook   : {dingtalk_api.mask_secret(getattr(incoming, 'session_webhook', ''))}")

        if text:
            # 用同一个 webhook 回一条，验证被动回复链路
            try:
                self.reply_text(f"收到：{text}", incoming)
                print("已通过 sessionWebhook 回复")
            except Exception as exc:  # noqa: BLE001
                print(f"sessionWebhook 回复失败：{exc}")

        self.received.set()
        return AckMessage.STATUS_OK, "OK"


async def _run_stream(seconds: float) -> int:
    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    handler = _PrintHandler()
    client.register_callback_handler(ChatbotMessage.TOPIC, handler)
    # 注册 EVENT 订阅，使建连时 subscriptions 带上 EVENT: *，后台验证连接通道才能通过
    client.register_all_event_handler(_VerifyEventHandler())

    # SDK 的 start() 内部是无限重连循环，不提供优雅停止接口，
    # 这里只等首条消息，进程退出交给 main() 的 os._exit。
    asyncio.create_task(client.start())
    print("Stream 连接中（已订阅 CALLBACK 机器人消息 + EVENT 事件）")
    print(f"等待消息（最多 {seconds:.0f} 秒）… 请给机器人发一条文字；"
          f"也可此时到后台点『验证连接通道』并保存")
    try:
        await asyncio.wait_for(handler.received.wait(), timeout=seconds)
        print("\nStream 收消息验证通过")
        return 0
    except asyncio.TimeoutError:
        print("\n超时未收到消息：确认应用已发布、机器人已添加、消息接收模式为 Stream")
        return 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="钉钉渠道阶段 1 冒烟")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="打印 SDK 版本与配置（默认）")
    sub.add_parser("token", help="验证 accessToken 链路")

    push = sub.add_parser("push", help="主动推送单聊消息")
    push.add_argument("--user-id", required=True, help="接收人明文 userId")
    push.add_argument("--text", default=DEFAULT_PUSH_TEXT)

    group = sub.add_parser("group", help="主动推送群聊消息")
    group.add_argument("--conversation-id", required=True, help="群 openConversationId")
    group.add_argument("--text", default=DEFAULT_PUSH_TEXT)

    stream = sub.add_parser("stream", help="连接 Stream 并打印回调帧")
    stream.add_argument("--seconds", type=float, default=120.0)

    replay = sub.add_parser("replay", help="重放同一回调帧验证 msgId 去重")
    replay.add_argument("--file", help="回调 JSON 文件；缺省使用内置样例")

    args = parser.parse_args()

    if args.command in (None, "check"):
        return cmd_check()
    if args.command == "token":
        return cmd_token()
    if args.command == "push":
        return cmd_push(args.user_id, args.text)
    if args.command == "group":
        return cmd_group(args.conversation_id, args.text)
    if args.command == "replay":
        data = SAMPLE_CALLBACK
        if args.file:
            loaded = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                print("回调文件内容不是 JSON 对象")
                return 1
            data = loaded
        return asyncio.run(
            _run_replay(data), loop_factory=asyncio.SelectorEventLoop
        )
    if args.command == "stream":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(_run_stream(args.seconds))
        # SDK 的 start() 会吞掉取消信号无限重连，直接结束进程保证脚本可退出
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
