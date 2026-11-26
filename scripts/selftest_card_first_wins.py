"""4.8 卡片 first-wins 离线自检：业务闸门 + 钉钉 ACK + 飞书响应内换卡。

不连数据库、不发请求：
1. 同一张卡连点不同任务 → 业务层只认第一次；
2. 钉钉 pick/refresh → ACK 内返回无按钮失效卡，不再异步更新；
3. 钉钉 text-only 坏帧 → 明确提示，不静默；
4. 飞书 pick/refresh → 响应卡置灰失效；通道层不重复消费业务闸门。

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_card_first_wins.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from lark_oapi.event.callback.model.p2_card_action_trigger import (  # noqa: E402
    P2CardActionTriggerResponse,
)

from app.channels.dingtalk import DingtalkChannel  # noqa: E402
from app.channels.feishu import FeishuChannel  # noqa: E402
from app.core import forms  # noqa: E402
from app.flows import aicc_task_flow  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


class _FakeChannel:
    name = "dingtalk"

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_card_to_chat(self, *args, **kwargs) -> None:
        return None

    async def send_text_to_chat(self, chat_id: str, text: str) -> None:
        self.texts.append(text)


class _Headers:
    topic = "/v1.0/card/instances/callback"


class _FakeCallback:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.headers = _Headers()


def _frame(user_id: str, out_track_id: str, params: dict) -> dict:
    content = {"cardPrivateData": {"actionIds": ["node-1"], "params": params}}
    return {
        "type": "actionCallback",
        "outTrackId": out_track_id,
        "corpId": "corp-selftest",
        "userId": user_id,
        "content": json.dumps(content, ensure_ascii=False),
    }


async def case_flow_first_wins() -> None:
    """同一张卡连点两个不同任务：只处理第一次。"""
    jobs: list[int] = []
    original = aicc_task_flow._send_status_overview

    async def fake_overview(channel, chat_id, token, job_id, filters, force=False):
        jobs.append(job_id)

    aicc_task_flow._send_status_overview = fake_overview
    token = forms.register(
        kind="status", step="list", operator="user-1",
        chat_id="user-1", message_id="card-list-1",
    )
    try:
        await asyncio.gather(
            aicc_task_flow.handle_action(
                _FakeChannel(), "task_status_pick",
                {"token": token, "job_id": "43"}, {}, "user-1", "user-1", "card-same-1",
            ),
            aicc_task_flow.handle_action(
                _FakeChannel(), "task_status_pick",
                {"token": token, "job_id": "44"}, {}, "user-1", "user-1", "card-same-1",
            ),
        )
    finally:
        aicc_task_flow._send_status_overview = original
        forms.pop_for(token)
    _check("同卡连点不同任务只处理第一次", jobs == [43])


async def case_dingtalk_ack_invalidation() -> None:
    """钉钉 pick：ACK 内返回无按钮失效卡。"""
    channel = DingtalkChannel()
    handled: list[tuple] = []

    async def handler(*args) -> None:
        handled.append(args)

    channel.set_form_handler(handler)
    frame = _frame("user-1", "dt-card-1", {"id": "task_status_pick|tok-1|42"})
    code, response = await channel.on_card_callback(_FakeCallback(frame))
    await asyncio.sleep(0.01)
    card_map = (
        response.get("cardData", {}).get("cardParamMap", {})
        if isinstance(response, dict)
        else {}
    )
    _check("钉钉 pick 返回 ACK 失效卡", isinstance(response, dict))
    _check(
        "失效卡使用 cardData 覆盖",
        isinstance(response, dict)
        and response.get("cardUpdateOptions", {}).get("updateCardDataByKey") is True,
    )
    _check("失效卡不再带按钮", "msgButtons" not in card_map)
    _check("失效卡进入完成态", str(card_map.get("flowStatus") or "") == "3")
    _check("业务动作仍被派发", len(handled) == 1)


async def case_dingtalk_text_only_frame() -> None:
    """重载态只剩 text：ACK 后补一条明确提示。"""
    channel = DingtalkChannel()
    channel.set_form_handler(lambda *args: None)
    sent: list[str] = []

    async def fake_send(user_id: str, text: str) -> None:
        sent.append(text)

    channel.send_text_to_chat = fake_send  # type: ignore[method-assign]
    frame = _frame("user-1", "dt-card-2", {"text": "查看 #42"})
    await channel.on_card_callback(_FakeCallback(frame))
    await asyncio.sleep(0.01)
    _check(
        "text-only 坏帧有明确提示",
        bool(sent) and "卡片已失效" in sent[0],
    )


class _FeishuContext:
    open_chat_id = "oc-selftest"
    open_message_id = "om-selftest-1"


async def case_feishu_visual_invalidation() -> None:
    """飞书 pick：响应内换失效卡；通道层不消费业务闸门。"""
    channel = object.__new__(FeishuChannel)
    calls: list[tuple] = []

    async def handler(*args) -> None:
        calls.append(args)

    channel._form_handler = handler
    channel._loop = asyncio.get_running_loop()

    pick = channel._handle_task_action(
        "task_status_pick",
        {"token": "tok-1", "job_id": "42"},
        {},
        "user-1",
        _FeishuContext(),
        P2CardActionTriggerResponse(),
    )
    await asyncio.sleep(0.01)
    _check("飞书 pick 返回响应内失效卡", getattr(pick, "card", None) is not None)
    _check("飞书 pick 仍派发业务", len(calls) == 1)

    again = channel._handle_task_action(
        "task_status_pick",
        {"token": "tok-1", "job_id": "43"},
        {},
        "user-1",
        _FeishuContext(),
        P2CardActionTriggerResponse(),
    )
    await asyncio.sleep(0.01)
    _check("飞书通道层不重复消费闸门", len(calls) == 2)
    _check("飞书重复点击仍返回失效卡", getattr(again, "card", None) is not None)

    page = channel._handle_task_action(
        "task_status_page",
        {"token": "tok-1", "page": "2"},
        {},
        "user-1",
        _FeishuContext(),
        P2CardActionTriggerResponse(),
    )
    _check("飞书翻页不换卡（保持可继续点）", getattr(page, "card", None) is None)


async def main() -> int:
    await case_flow_first_wins()
    await case_dingtalk_ack_invalidation()
    await case_dingtalk_text_only_frame()
    await case_feishu_visual_invalidation()
    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print("=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
