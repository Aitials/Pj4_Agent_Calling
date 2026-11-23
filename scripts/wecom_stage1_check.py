"""企业微信智能机器人 · 阶段 1 冒烟测试。

覆盖四项验收：
1. 长连接认证成功
2. 被动回复（给机器人发消息后流式回一条）——仅 --listen 模式
3. send_message 主动推送 Markdown
4. 发送 button_interaction 模板卡 → 收到 template_card_event → 5 秒内原地更新

用法：
    # 已知自己的企微 userid：直接跑推送 + 卡片测试
    python scripts/wecom_stage1_check.py --userid zhangsan

    # 不知道 userid：先给机器人发任意一条消息，脚本自动取发送人 userid
    python scripts/wecom_stage1_check.py --listen

注意：同一个 BotID 只允许一条长连接。运行前请先停掉 run_wecom.py，
否则两边会互相踢连接，表现为消息时有时无。
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

from aibot import generate_req_id  # noqa: E402

from app.clients import wecom_api  # noqa: E402

log = logging.getLogger("wecom_check")

CARD_UPDATE_LIMIT_MS = 5000


def _body(frame: dict) -> dict:
    body = frame.get("body")
    return body if isinstance(body, dict) else {}


def _userid_of(body: dict) -> str:
    sender = body.get("from")
    if isinstance(sender, dict):
        return str(sender.get("userid") or sender.get("user_id") or "")
    return ""


def _ack_error(resp: object) -> str:
    """把 send_message / update_template_card 的回执错误转成可读文本。"""
    if isinstance(resp, dict):
        errcode = resp.get("errcode")
        if errcode not in (0, None):
            return f"errcode={errcode} errmsg={resp.get('errmsg') or ''}"
    return ""


async def _run(userid: str, listen: bool, timeout: float) -> int:
    client = wecom_api.build_ws_client()
    authed = asyncio.Event()
    inbound = asyncio.Event()
    clicked = asyncio.Event()

    state: dict[str, object] = {
        "target": userid,
        "inbound_frame": None,
        "event_key": "",
        "task_id": "",
        "update_ms": -1,
        "update_ok": False,
    }

    client.on("authenticated", authed.set)
    client.on("disconnected", lambda reason: log.warning("长连接断开：%s", reason))
    client.on("error", lambda exc: log.error("长连接异常：%s", exc))

    async def on_message(frame: dict) -> None:
        if state["inbound_frame"] is not None:
            return
        body = _body(frame)
        sender = _userid_of(body)
        if not sender:
            return
        state["inbound_frame"] = frame
        state["target"] = sender
        log.info(
            "收到入站消息 userid=%s msgtype=%s text=%s",
            sender,
            body.get("msgtype"),
            (body.get("text") or {}).get("content") if isinstance(body.get("text"), dict) else "",
        )
        inbound.set()

    async def on_card_event(frame: dict) -> None:
        body = _body(frame)
        event = body.get("event")
        event = event if isinstance(event, dict) else {}
        state["event_key"] = str(event.get("event_key") or "")
        state["task_id"] = str(event.get("task_id") or "")
        log.info(
            "收到卡片回调 operator=%s event_key=%s task_id=%s",
            _userid_of(body),
            state["event_key"] or "-",
            state["task_id"] or "-",
        )

        started = time.monotonic()
        updated = {
            "card_type": "button_interaction",
            "task_id": state["task_id"] or f"stage1_{int(time.time())}",
            "main_title": {
                "title": "回调验证成功",
                "desc": f"event_key={state['event_key'] or '-'}",
            },
            "button_list": [{"text": "已收到", "key": "stage1_done", "style": 1}],
        }
        try:
            ack = await client.update_template_card(frame, updated)
            state["update_ms"] = int((time.monotonic() - started) * 1000)
            error = _ack_error(ack)
            state["update_ok"] = not error
            log.info(
                "原地更新卡片%s，耗时 %d ms%s",
                "成功" if state["update_ok"] else "失败",
                state["update_ms"],
                f"，{error}" if error else "",
            )
        except Exception:  # noqa: BLE001
            log.exception("原地更新卡片异常")
        finally:
            clicked.set()

    client.on("message", on_message)
    client.on("event.template_card_event", on_card_event)

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
    log.info("PASS 1/4 长连接认证成功")

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
        frame = state["inbound_frame"]
        if isinstance(frame, dict):
            try:
                await client.reply_stream(
                    frame,
                    generate_req_id("stream"),
                    "阶段 1 测试：已收到你的消息，接下来会发主动推送和测试卡片。",
                    True,
                )
                log.info("PASS 2/4 被动回复已发送")
            except Exception:  # noqa: BLE001
                log.exception("被动回复失败")
                client.disconnect()
                return 1
        else:
            log.warning("未拿到入站帧，跳过被动回复验证")

    try:
        ack = await client.send_message(
            target,
            {
                "msgtype": "markdown",
                "markdown": {"content": "**阶段 1 测试**：这是一条主动推送消息。"},
            },
        )
        error = _ack_error(ack)
        if error:
            log.error("主动推送失败：%s", error)
            client.disconnect()
            return 1
        log.info("PASS 3/4 主动推送已提交 userid=%s", target)
    except Exception:  # noqa: BLE001
        log.exception("主动推送异常")
        client.disconnect()
        return 1

    task_id = f"stage1_{int(time.time())}"
    card = {
        "card_type": "button_interaction",
        "main_title": {"title": "阶段 1 回调测试", "desc": "请点击下方任意按钮"},
        "button_list": [
            {"text": "确认", "key": "stage1_ok", "style": 1},
            {"text": "取消", "key": "stage1_cancel", "style": 2},
        ],
        "task_id": task_id,
    }
    try:
        ack = await client.send_message(target, {"msgtype": "template_card", "template_card": card})
        error = _ack_error(ack)
        if error:
            log.error("发送测试卡失败：%s", error)
            client.disconnect()
            return 1
        log.info("测试卡已发送 task_id=%s，请在企微里点击按钮", task_id)
    except Exception:  # noqa: BLE001
        log.exception("发送测试卡异常")
        client.disconnect()
        return 1

    try:
        await asyncio.wait_for(clicked.wait(), timeout)
    except asyncio.TimeoutError:
        log.error("等待卡片回调超时（%.0f 秒）", timeout)
        client.disconnect()
        return 1

    if not state["event_key"]:
        log.error("FAIL 4/4 回调里没有 event_key，实际帧请查看上方日志")
        client.disconnect()
        return 1
    log.info("PASS 4/4 收到卡片回调 event_key=%s", state["event_key"])

    if state["update_ok"] and 0 <= int(state["update_ms"]) <= CARD_UPDATE_LIMIT_MS:
        log.info("PASS 5/5 原地更新卡片成功（%d ms，5 秒窗口内）", state["update_ms"])
        code = 0
    else:
        log.error(
            "FAIL 5/5 原地更新失败或超时（ok=%s，%d ms，上限 %d ms）",
            state["update_ok"],
            state["update_ms"],
            CARD_UPDATE_LIMIT_MS,
        )
        code = 1

    client.disconnect()
    return code


def main() -> None:
    parser = argparse.ArgumentParser(description="企业微信智能机器人阶段 1 冒烟测试")
    parser.add_argument("--userid", default="", help="接收测试消息的企微成员 userid")
    parser.add_argument(
        "--listen",
        action="store_true",
        help="不传 userid 时监听入站消息，自动取发送人 userid 并验证被动回复",
    )
    parser.add_argument("--timeout", type=float, default=120, help="等待入站/回调的秒数，默认 120")
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
