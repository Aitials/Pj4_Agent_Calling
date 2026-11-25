"""离线自检：钉钉卡片回调底座（阶段 4.2，不依赖网络/数据库）。

覆盖：
1. 表单回调：字段类型归一化、token/__action 提取、ACK 响应内禁用表单；
2. 按钮回调：`action|token|extra` 解析与 extra 字段映射；
3. 取消按钮：token 作废、卡片收敛；
4. 非法帧：缺 token/__action 或坏按钮 id 时只 ACK，不派发业务；
5. 重复按钮回调去重；概况查询同任务单次执行、同会话串行。

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_dingtalk_cards.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.channels.dingtalk import DingtalkChannel  # noqa: E402
from app.clients import dingtalk_api  # noqa: E402
from app.cards import aicc_task_cards_dingtalk as dingtalk_cards  # noqa: E402
from app.core import forms  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


class _Headers:
    topic = "/v1.0/card/instances/callback"


class _FakeCallback:
    def __init__(self, data: dict) -> None:
        self.data = data
        self.headers = _Headers()


def _frame(user_id: str, out_track_id: str, params: dict) -> dict:
    content = {
        "cardPrivateData": {
            "actionIds": ["node-1"],
            "params": params,
        }
    }
    return {
        "type": "actionCallback",
        "outTrackId": out_track_id,
        "corpId": "corp-selftest",
        "userId": user_id,
        "content": json.dumps(content, ensure_ascii=False),
    }


def _form_params() -> dict:
    return {
        "form": {
            "name": "测试任务",
            "lines": {"index": [0], "value": ["line-a"]},
            "count": 2,
            "agree": True,
            "token": "tok-form-1",
            "__action": "task_create_step1",
        },
        "current_form": {
            "fields": [
                {"name": "name", "type": "TEXT"},
                {"name": "lines", "type": "MULTI_SELECT"},
                {"name": "count", "type": "NUMBER"},
                {"name": "agree", "type": "CHECKBOX"},
                {"name": "token", "type": "TEXT", "hidden": True},
                {"name": "__action", "type": "TEXT", "hidden": True},
            ]
        },
    }


class _FakeChannel:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def send_text_to_chat(self, chat_id: str, text: str) -> None:
        self.texts.append(text)


async def case_form_callback() -> None:
    calls: list[tuple] = []

    async def handler(action, value, form_value, operator, chat_id, message_id):
        calls.append((action, value, form_value, operator, chat_id, message_id))

    channel = DingtalkChannel()
    channel.set_form_handler(handler)

    ack = await channel.on_card_callback(
        _FakeCallback(_frame("user-1", "card-form-1", _form_params()))
    )
    await asyncio.sleep(0.05)

    _check("表单回调 ACK 200", ack[0] == 200)
    response = ack[1] if isinstance(ack[1], dict) else {}
    private = (response.get("userPrivateData") or {}).get("cardParamMap") or {}
    _check("响应内 form_status=disabled", private.get("form_status") == "disabled")
    rendered = json.loads(private.get("form") or "{}")
    fields = {item["name"]: item for item in rendered.get("fields") or []}
    _check(
        "回显 name 默认值",
        fields.get("name", {}).get("defaultValue") == "测试任务",
    )
    _check(
        "回显 MULTI_SELECT 原结构",
        fields.get("lines", {}).get("defaultValue")
        == {"index": [0], "value": ["line-a"]},
    )

    _check("表单派发一次", len(calls) == 1)
    if calls:
        action, value, form_value, operator, chat_id, message_id = calls[0]
        _check("action 来自 __action", action == "task_create_step1")
        _check("token 正确", value.get("token") == "tok-form-1")
        _check("MULTI_SELECT 归一化为数组", form_value.get("lines") == ["line-a"])
        _check("NUMBER 归一化为数字", form_value.get("count") == 2)
        _check("CHECKBOX 归一化为布尔", form_value.get("agree") is True)
        _check("token/__action 不进入 form_value", "token" not in form_value and "__action" not in form_value)
        _check("operator/chat_id 为明文 userId", operator == "user-1" and chat_id == "user-1")


async def case_button_callback() -> None:
    calls: list[tuple] = []

    async def handler(action, value, form_value, operator, chat_id, message_id):
        calls.append((action, value, form_value, operator, chat_id, message_id))

    channel = DingtalkChannel()
    channel.set_form_handler(handler)

    ack = await channel.on_card_callback(
        _FakeCallback(
            _frame(
                "user-1",
                "card-btn-1",
                {"id": "task_status_page|tok-2|3", "text": "下一页"},
            )
        )
    )
    await asyncio.sleep(0.05)

    _check("按钮回调 ACK OK", ack == (200, "OK"))
    _check("按钮派发一次", len(calls) == 1)
    if calls:
        action, value, form_value, operator, _, _ = calls[0]
        _check("action 解析正确", action == "task_status_page")
        _check("token 解析正确", value.get("token") == "tok-2")
        _check("extra 映射到 page", value.get("page") == "3")
        _check("form_value 为空", form_value == {})


async def case_button_callback_dedup() -> None:
    """平台重投/连点：同一张卡同一按钮，短窗口内只处理第一次。"""
    calls: list[tuple] = []

    async def handler(*args):
        calls.append(args)

    channel = DingtalkChannel()
    channel.set_form_handler(handler)
    frame = _frame("user-1", "card-dup-1", {"id": "task_status_page|tok-9|2"})

    await channel.on_card_callback(_FakeCallback(frame))
    await channel.on_card_callback(_FakeCallback(frame))
    await asyncio.sleep(0.05)
    _check("重复按钮回调只派发一次", len(calls) == 1)


async def case_status_pick_guard() -> None:
    """概况查询：同任务并发点击只查一次，不同任务按会话串行。"""
    from app.flows import aicc_task_flow

    state = {"active": 0, "peak": 0, "jobs": []}
    original = aicc_task_flow._send_status_overview

    async def slow_overview(channel, chat_id, token, job_id, filters, force=False):
        state["jobs"].append(job_id)
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.1)
        state["active"] -= 1

    aicc_task_flow._send_status_overview = slow_overview
    channel = _FakeChannel()
    token = forms.register(
        kind="status",
        step="list",
        operator="user-1",
        chat_id="user-1",
        message_id="card-list-1",
    )
    try:
        await asyncio.gather(
            aicc_task_flow.handle_action(
                channel, "task_status_pick", {"token": token, "job_id": "42"},
                {}, "user-1", "user-1", "card-list-1",
            ),
            aicc_task_flow.handle_action(
                channel, "task_status_pick", {"token": token, "job_id": "42"},
                {}, "user-1", "user-1", "card-list-1",
            ),
        )
        _check("同一任务并发点击只查一次", state["jobs"] == [42])

        state["jobs"].clear()
        state["peak"] = 0
        await asyncio.gather(
            aicc_task_flow.handle_action(
                channel, "task_status_pick", {"token": token, "job_id": "43"},
                {}, "user-1", "user-1", "card-list-1",
            ),
            aicc_task_flow.handle_action(
                channel, "task_status_pick", {"token": token, "job_id": "44"},
                {}, "user-1", "user-1", "card-list-1",
            ),
        )
        _check(
            "不同任务按会话串行",
            state["peak"] == 1 and sorted(state["jobs"]) == [43, 44],
        )
    finally:
        aicc_task_flow._send_status_overview = original
        forms.pop_for(token)


async def case_cancel_and_bad_frames() -> None:
    calls: list[tuple] = []

    async def handler(*args):
        calls.append(args)

    channel = DingtalkChannel()
    channel.set_form_handler(handler)

    token = forms.register(
        kind="create",
        step="confirm",
        operator="user-1",
        chat_id="user-1",
        message_id="m-1",
    )
    ack = await channel.on_card_callback(
        _FakeCallback(
            _frame(
                "user-1",
                "card-cancel-1",
                {"id": f"task_form_cancel|{token}", "text": "取消"},
            )
        )
    )
    await asyncio.sleep(0.05)
    _check("取消 ACK OK", ack == (200, "OK"))
    _check("取消不派发业务", calls == [])
    _check("取消后 token 已作废", forms.get_for(token)[1] == "missing")

    bad_form = _form_params()
    bad_form["form"].pop("__action")
    ack = await channel.on_card_callback(
        _FakeCallback(_frame("user-1", "card-form-bad", bad_form))
    )
    await asyncio.sleep(0.05)
    _check("缺 __action 只 ACK", ack == (200, "OK"))
    _check("缺 __action 不派发", calls == [])

    ack = await channel.on_card_callback(
        _FakeCallback(_frame("user-1", "card-btn-bad", {"id": "no-token"}))
    )
    await asyncio.sleep(0.05)
    _check("坏按钮 id 只 ACK", ack == (200, "OK"))
    _check("坏按钮 id 不派发", calls == [])


async def case_build_step1() -> None:
    """4.3 骨架：step1 表单字段与隐藏 token/action。"""
    card = dingtalk_cards.build_create_form_step1(
        token="tok-build-1",
        lines={"固话": [{"tenantPhoneNumberId": "101", "phoneNumber": "0571-1234"}]},
        bots=[{"id": 7, "name": "回访BOT"}],
    )
    _check("step1 是 form 卡", card.get("kind") == "form")
    _check(
        "step1 隐藏 token/__action",
        card.get("hidden") == {"token": "tok-build-1", "__action": "task_create_step1"},
    )
    fields = {item["name"]: item for item in card.get("fields") or []}
    _check(
        "step1 字段齐全",
        set(fields)
        == {
            "name",
            "dialog_flow_id",
            "phone_number_ids",
            "concurrency_quota",
            "robot_count",
            "daily_start_time",
            "daily_end_time",
            "days_of_week",
        },
    )
    _check("话术为 SELECT", fields.get("dialog_flow_id", {}).get("type") == "SELECT")
    _check(
        "话术选项正确",
        fields.get("dialog_flow_id", {}).get("options") == [{"text": "回访BOT", "value": "7"}],
    )
    _check(
        "线路为 MULTI_SELECT",
        fields.get("phone_number_ids", {}).get("type") == "MULTI_SELECT",
    )
    _check(
        "线路选项正确",
        (fields.get("phone_number_ids", {}).get("options") or [{}])[0].get("value")
        == "101",
    )
    _check("并发默认 1", fields.get("concurrency_quota", {}).get("defaultValue") == 1)
    _check("按钮文案为下一步", card.get("btn_text") == "下一步")

    error_card = dingtalk_cards.build_create_form_step1(
        token="tok-build-2",
        lines={},
        bots=[{"id": 7, "name": "回访BOT"}],
        error="并发数超过线路并发上限",
    )
    error_fields = error_card.get("fields") or []
    _check("错误字段在最前", error_fields and error_fields[0].get("name") == "__error")
    _check(
        "错误文案正确",
        error_fields and error_fields[0].get("defaultValue") == "并发数超过线路并发上限",
    )

    step2 = dingtalk_cards.build_create_form_step2(
        token="tok-build-2",
        params={
            "name": "测试任务",
            "dialog_flow_name": "回访BOT",
            "dialog_flow_id": 7,
            "phone_number_ids": [101],
            "concurrency_quota": 1,
            "robot_count": 1,
            "daily_start_time": "09:00:00",
            "daily_end_time": "18:00:00",
            "days_of_week": ["MONDAY"],
        },
        staff_groups=[{"id": 1, "name": "坐席组A"}],
        sms_templates=[{"id": 2, "name": "模板A"}],
        white_groups=[],
        folders=[{"id": 3, "name": "文件夹A"}],
    )
    _check("step2 是 form 卡", step2.get("kind") == "form")
    _check(
        "step2 隐藏 __action",
        (step2.get("hidden") or {}).get("__action") == "task_create_step2",
    )
    s2 = {item["name"]: item for item in step2.get("fields") or []}
    _check(
        "step2 字段齐全",
        {
            "__summary",
            "mode",
            "priority",
            "on_working_days",
            "description",
            "folder_id",
            "redial_times",
            "redial_interval",
            "redial_condition",
            "cs_staff_group_id",
            "sms_template_id",
            "customer_white_group_ids",
            "enable_elastic_robot",
        }
        <= set(s2),
    )
    _check("step2 摘要只读", s2.get("__summary", {}).get("readOnly") is True)
    _check(
        "step2 坐席选项含不转人工",
        (s2.get("cs_staff_group_id", {}).get("options") or [{}])[0].get("value") == "0",
    )
    _check(
        "step2 白名单空时退化为文本框",
        s2.get("customer_white_group_ids", {}).get("type") == "TEXT",
    )
    _check("step2 按钮文案", step2.get("btn_text") == "下一步（留空取默认值）")

    confirm = dingtalk_cards.build_task_confirm(
        token="tok-build-2",
        kind="create",
        fields=[("任务名称", "测试任务"), ("并发 / BOT", "1 / 1")],
    )
    _check("确认卡为 buttons", confirm.get("kind") == "buttons")
    _check(
        "确认卡按钮 id 正确",
        [item.get("id") for item in confirm.get("buttons") or []]
        == ["task_form_cancel|tok-build-2", "task_create_confirm|tok-build-2"],
    )
    _check(
        "确认卡含摘要",
        "任务名称" in str(confirm.get("markdown") or ""),
    )

    start = dingtalk_cards.build_start_form(
        token="tok-start",
        jobs=[{"id": 1704345657450, "name": "123测试", "status": "NOT_STARTED"}],
    )
    _check("启动卡是 form", start.get("kind") == "form")
    _check(
        "启动卡隐藏 __action",
        (start.get("hidden") or {}).get("__action") == "task_start_step1",
    )
    start_fields = {item["name"]: item for item in start.get("fields") or []}
    _check(
        "启动卡含下拉与手填",
        {"robot_call_job_id_select", "robot_call_job_id"} <= set(start_fields),
    )
    _check(
        "启动卡选项正确",
        (start_fields.get("robot_call_job_id_select", {}).get("options") or [{}])[0].get(
            "value"
        )
        == "1704345657450",
    )
    no_jobs = dingtalk_cards.build_start_form(token="tok-start-2", jobs=[])
    _check(
        "无任务时只保留手填",
        "robot_call_job_id_select"
        not in {item["name"] for item in no_jobs.get("fields") or []},
    )
    start_confirm = dingtalk_cards.build_task_confirm(
        token="tok-start",
        kind="start",
        fields=[("任务 ID", 1704345657450)],
    )
    _check(
        "启动确认按钮 id 正确",
        [item.get("id") for item in start_confirm.get("buttons") or []]
        == ["task_form_cancel|tok-start", "task_start_confirm|tok-start"],
    )


async def case_build_status_cards() -> None:
    """4.6：筛选表单、列表按钮、概况按钮。"""
    filters = dingtalk_cards.build_status_filter(token="tok-status")
    _check("筛选卡是 form", filters.get("kind") == "form")
    _check(
        "筛选卡隐藏 __action",
        (filters.get("hidden") or {}).get("__action") == "task_status_query",
    )
    filter_fields = {item["name"]: item for item in filters.get("fields") or []}
    _check(
        "筛选卡字段齐全",
        {"status_filter", "order_type", "days"} <= set(filter_fields),
    )
    _check(
        "状态筛选项含 ALL",
        any(
            item.get("value") == "ALL"
            for item in filter_fields.get("status_filter", {}).get("options") or []
        ),
    )

    jobs = [
        {
            "id": index,
            "name": f"任务{index}",
            "status": "NOT_STARTED",
            "status_label": "未开始",
            "create_time": "2026-11-25 10:00:00",
            "dialog_flow_name": "测试BOT",
        }
        for index in range(1, 8)
    ]
    first = dingtalk_cards.build_status_list(
        token="tok-status", jobs=jobs, page=1, filters_text="筛选：全部"
    )
    first_ids = [item.get("id") for item in first.get("buttons") or []]
    _check("列表卡是 buttons", first.get("kind") == "buttons")
    _check(
        "第一页 5 个任务按钮",
        len([item for item in first_ids if str(item).startswith("task_status_pick|")])
        == 5,
    )
    _check(
        "第一页有下一页按钮",
        "task_status_page|tok-status|2" in first_ids,
    )
    _check("列表卡含总数", "共 **7** 个任务" in str(first.get("markdown") or ""))

    second = dingtalk_cards.build_status_list(
        token="tok-status", jobs=jobs, page=2, filters_text="筛选：全部"
    )
    second_ids = [item.get("id") for item in second.get("buttons") or []]
    _check(
        "第二页 2 个任务按钮",
        len([item for item in second_ids if str(item).startswith("task_status_pick|")])
        == 2,
    )
    _check(
        "第二页有上一页按钮",
        "task_status_page|tok-status|1" in second_ids,
    )

    overview = {
        "job_id": 1704345657450,
        "name": "123测试",
        "status": "NOT_STARTED",
        "status_label": "未开始",
        "ended": False,
        "progress": {"total": 100, "completed": 10, "filtered": 2, "percent": 10.0},
        "call": {
            "total": 20,
            "answered": 10,
            "rate": 50.0,
            "avg_duration": 30.0,
            "hangup_rate": 10.0,
            "ab_ratio": 5.0,
            "last_call_date": "-",
        },
        "intents": [{"name": "A 类", "count": 5, "share": 50.0}],
        "config": {
            "dialog_flow": "测试BOT",
            "lines": "101",
            "concurrency": 1,
            "robot_count": 1,
            "daily": "09:00:00 - 18:00:00",
            "days": "周一",
            "priority": 100,
            "created_by": "测试",
        },
        "hints": ["测试提示"],
        "fetched_at": "2026-11-25 12:00:00",
    }
    card = dingtalk_cards.build_status_overview(token="tok-status", overview=overview)
    card_ids = [item.get("id") for item in card.get("buttons") or []]
    _check(
        "概况卡四个按钮",
        set(card_ids)
        == {
            "task_status_refresh|tok-status|1704345657450",
            "task_status_back|tok-status|list",
            "task_status_filter|tok-status",
            "task_form_cancel|tok-status",
        },
    )
    markdown = str(card.get("markdown") or "")
    _check(
        "概况卡含关键段落",
        all(word in markdown for word in ("状态与进度", "通话效果", "意向分布", "配置摘要")),
    )


async def case_button_card_streams_body() -> None:
    """按钮卡必须先推正文再收尾，否则卡片只显示标题和按钮。"""
    import app.channels.dingtalk as dingtalk_channel

    calls: list[tuple] = []

    class FakeCard:
        def __init__(self, client, message) -> None:
            self.card_instance_id = "card-btn-1"
            self.last_stream_ok = True
            self.last_update_ok = True

        def set_title_and_logo(self, title, logo) -> None:
            calls.append(("title", title))

        def ai_start(self) -> None:
            calls.append(("start",))

        def ai_streaming(self, text: str) -> None:
            calls.append(("stream", text))

        def ai_streaming_final(self, text: str) -> None:
            calls.append(("stream_final", text))

        def ai_finish(self, text: str, buttons: list) -> None:
            calls.append(("finish", text, buttons))

    old = dingtalk_channel._ObservableAiCard
    dingtalk_channel._ObservableAiCard = FakeCard
    try:
        channel = DingtalkChannel()
        channel._client = object()
        cid = await channel._send_button_card(
            "user-1",
            {
                "kind": "buttons",
                "title": "按钮卡",
                "markdown": "卡片正文",
                "buttons": [
                    {
                        "text": "确认",
                        "id": "task_create_confirm|tok",
                    }
                ],
            },
        )
    finally:
        dingtalk_channel._ObservableAiCard = old

    _check("按钮卡返回实例 ID", cid == "card-btn-1")
    _check("按钮卡先推正文", ("stream_final", "卡片正文") in calls)
    kinds = [item[0] for item in calls]
    _check("推正文在收尾之前", kinds.index("stream_final") < kinds.index("finish"))
    finish = next(item for item in calls if item[0] == "finish")
    _check(
        "收尾带按钮",
        bool(finish[2]) and finish[2][0].get("id") == "task_create_confirm|tok",
    )


async def main() -> int:
    # 离线：禁止回调内的换卡发出真实请求
    dingtalk_api.update_card = lambda *args, **kwargs: True

    await case_build_step1()
    await case_build_status_cards()
    await case_button_card_streams_body()
    await case_button_callback_dedup()
    await case_status_pick_guard()
    await case_form_callback()
    await case_button_callback()
    await case_cancel_and_bad_frames()

    if _failures:
        print(f"\n=== 失败 {len(_failures)} 项：{', '.join(_failures)} ===")
        return 1
    print("\n=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
