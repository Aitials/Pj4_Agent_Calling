"""企业微信智能机器人 · 阶段 2 冒烟测试（通道层卡片发送）。

验证：
1. 长连接认证
2. send_card_to_chat 参数校验（空卡片被拦截）
3. send_card_to_chat 主动发送 text_notice 模板卡

用法：
    # 已知自己的企微 userid
    python scripts/wecom_stage2_check.py --userid zhangsan

    # 不知道 userid：先给机器人发一条消息，脚本自动取发送人
    python scripts/wecom_stage2_check.py --listen

注意：同一个 BotID 只允许一条长连接，运行前请先停掉 run_wecom.py。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.channels.wecom import WecomChannel  # noqa: E402
from app.clients import wecom_api  # noqa: E402

log = logging.getLogger("wecom_stage2")


def _sender_of(frame: dict) -> str:
    body = frame.get("body")
    if not isinstance(body, dict):
        return ""
    sender = body.get("from")
    if isinstance(sender, dict):
        return str(sender.get("userid") or sender.get("user_id") or "")
    return ""


async def _run(userid: str, listen: bool, timeout: float) -> int:
    client = wecom_api.build_ws_client()
    channel = WecomChannel()
    channel._client = client  # 复用通道层发送逻辑，不启动 start()

    authed = asyncio.Event()
    inbound = asyncio.Event()
    state = {"target": userid}

    client.on("authenticated", authed.set)
    client.on("disconnected", lambda reason: log.warning("长连接断开：%s", reason))
    client.on("error", lambda exc: log.error("长连接异常：%s", exc))

    async def on_message(frame: dict) -> None:
        sender = _sender_of(frame)
        if sender:
            state["target"] = sender
            log.info("收到入站消息 userid=%s", sender)
            inbound.set()

    client.on("message", on_message)

    try:
        await client.connect()
    except Exception:  # noqa: BLE001
        log.exception("建立长连接失败")
        return 1

    try:
        await asyncio.wait_for(authed.wait(), 20)
    except asyncio.TimeoutError:
        log.error("20 秒内未收到认证成功，请检查 WECHAT_BOT_ID / WECHAT_BOT_SECRET")
        client.disconnect()
        return 1
    log.info("PASS 1/3 长连接认证成功")

    target = str(state["target"] or "")
    if listen or not target:
        if not target:
            log.info("请在企微里给机器人发送任意一条消息，脚本会自动读取你的 userid")
        try:
            await asyncio.wait_for(inbound.wait(), timeout)
        except asyncio.TimeoutError:
            log.error("等待入站消息超时（%.0f 秒）", timeout)
            client.disconnect()
            return 1
        target = str(state["target"])

    try:
        await channel.send_card_to_chat(target, {})
    except ValueError:
        log.info("PASS 2/3 空卡片被参数校验拦截")
    else:
        log.error("FAIL 2/3 空卡片未被拦截")
        client.disconnect()
        return 1

    card = {
        "card_type": "text_notice",
        "main_title": {
            "title": "阶段 2 卡片测试",
            "desc": "send_card_to_chat 验证",
        },
        "sub_title_text": "看到这张卡片，说明企微通道层可以主动发送模板卡片。",
        # 企微 text_notice 强制要求 card_action，缺失会报 42045
        "card_action": {"type": 1, "url": "https://work.weixin.qq.com/"},
    }
    try:
        await channel.send_card_to_chat(target, card)
    except Exception:  # noqa: BLE001
        log.exception("发送模板卡片失败")
        client.disconnect()
        return 1

    log.info("PASS 3/3 模板卡片已提交 userid=%s，请在企微里确认", target)
    await asyncio.sleep(2)
    client.disconnect()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="企业微信阶段 2 冒烟测试")
    parser.add_argument("--userid", default="", help="接收测试卡片的企微成员 userid")
    parser.add_argument("--listen", action="store_true", help="监听入站消息并自动取发送人 userid")
    parser.add_argument("--timeout", type=float, default=120, help="等待入站消息的秒数，默认 120")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not wecom_api.is_configured():
        print("缺少 WECHAT_BOT_ID / WECHAT_BOT_SECRET，请先在 .env 中配置", file=sys.stderr)
        raise SystemExit(2)

    code = asyncio.run(
        _run(args.userid.strip(), args.listen, args.timeout),
        loop_factory=asyncio.SelectorEventLoop,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
