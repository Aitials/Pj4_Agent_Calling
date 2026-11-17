"""创建 / 启动外呼任务的卡片流程编排。

交互层（本模块 + 卡片 JSON）负责收集参数、二次确认；
执行层（app.tools.aicc_task）只关心「参数合法就调接口」。
"""

from __future__ import annotations

import asyncio
import logging

from app.cards import aicc_task_cards as cards
from app.clients import aicc_options
from app.core import forms
from app.tools import aicc_task

log = logging.getLogger("aicc_flow")


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.replace("，", ",").strip()
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def _as_int(value, label: str, default: int | None = None) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        if default is None:
            raise ValueError(f"{label}不能为空")
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字") from exc


def _as_bool(value, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text in ("true", "1", "yes", "是", "开启")


async def open_create_form(channel, chat_id: str, operator: str, message_id: str = "") -> None:
    token = forms.register(
        kind="create",
        step="step1",
        operator=operator,
        chat_id=chat_id,
        message_id=message_id,
    )
    lines = await asyncio.to_thread(aicc_options.phone_lines)
    flows = await asyncio.to_thread(aicc_options.dialog_flows)
    card = cards.build_create_form_step1(token=token, lines=lines, dialog_flows=flows)
    await channel.send_card_to_chat(chat_id, card)


async def open_start_form(channel, chat_id: str, operator: str, message_id: str = "") -> None:
    token = forms.register(
        kind="start",
        step="step1",
        operator=operator,
        chat_id=chat_id,
        message_id=message_id,
    )
    jobs = await asyncio.to_thread(aicc_options.startable_jobs)
    card = cards.build_start_form(token=token, jobs=jobs)
    await channel.send_card_to_chat(chat_id, card)


async def handle_action(
    channel,
    action: str,
    value: dict,
    form_value: dict | None,
    operator: str,
    chat_id: str,
    message_id: str,
) -> None:
    """卡片回调的异步处理入口（由 channel 调度到事件循环）。"""
    form_value = form_value or {}
    token = str(value.get("token") or "")
    log.info(
        "任务卡片回调 action=%s token=%s operator=%s fields=%s",
        action, token, operator, list(form_value.keys()),
    )

    try:
        if action in ("debug_open_create", "open_create"):
            await open_create_form(channel, chat_id, operator, message_id)
            return
        if action in ("debug_open_start", "open_start"):
            await open_start_form(channel, chat_id, operator, message_id)
            return

        if action == "task_form_refresh":
            kind = str(value.get("kind") or "create")
            if kind == "start":
                await open_start_form(channel, chat_id, operator, message_id)
            else:
                await open_create_form(channel, chat_id, operator, message_id)
            return

        if action == "task_create_step1":
            await _create_step1(channel, token, form_value, operator, chat_id, message_id)
            return
        if action in ("task_create_step2", "task_create_skip"):
            await _create_step2(
                channel, token, form_value, operator, chat_id, message_id,
                skip=action == "task_create_skip",
            )
            return
        if action == "task_create_confirm":
            await _create_confirm(channel, token, operator, chat_id)
            return

        if action == "task_start_step1":
            await _start_step1(channel, token, form_value, operator, chat_id, message_id)
            return
        if action == "task_start_confirm":
            await _start_confirm(channel, token, operator, chat_id)
            return

        log.warning("未知的卡片动作：%s", action)
    except Exception:  # noqa: BLE001
        log.exception("处理卡片动作失败 action=%s", action)
        try:
            await channel.send_text_to_chat(chat_id, "处理失败，请稍后重试或查看日志。")
        except Exception:  # noqa: BLE001
            log.exception("发送失败提示也失败")


def _require_record(token: str, operator: str, expect_kind: str) -> dict | None:
    record, reason = forms.get_for(token, operator)
    if reason == "ok" and record:
        if record.get("kind") != expect_kind:
            return None
        return record
    return None


def _parse_step1(form_value: dict) -> dict:
    name = str(form_value.get("name") or "").strip()
    if not name:
        raise ValueError("任务名称不能为空")

    dialog_flow_id = _as_int(form_value.get("dialog_flow_id"), "话术 / BOT")
    ids = [int(item) for item in _as_list(form_value.get("phone_number_ids"))]
    if not ids:
        raise ValueError("主叫线路不能为空")

    concurrency = _as_int(form_value.get("concurrency_quota"), "线路并发数", 1)
    robots = _as_int(form_value.get("robot_count"), "BOT 数量", 1)
    if concurrency < 1 or robots < 1:
        raise ValueError("并发数与 BOT 数量都必须大于等于 1")

    start_time = aicc_task.normalize_time(form_value.get("daily_start_time"), "每日开始时间")
    end_time = aicc_task.normalize_time(form_value.get("daily_end_time"), "每日结束时间")
    if start_time >= end_time:
        raise ValueError("每日开始时间必须早于结束时间")

    days = aicc_task.normalize_days(_as_list(form_value.get("days_of_week")))
    phone_type = aicc_task.line_type_of(ids)
    _usable, limit = aicc_task.check_lines(ids, phone_type)
    if limit and concurrency > limit:
        raise ValueError(f"并发数 {concurrency} 超过线路并发上限 {limit}")

    return {
        "name": name,
        "dialog_flow_id": dialog_flow_id,
        "phone_number_ids": ids,
        "concurrency_quota": concurrency,
        "robot_count": robots,
        "daily_start_time": start_time,
        "daily_end_time": end_time,
        "days_of_week": days,
        "phone_type": phone_type,
    }


def _parse_step2(form_value: dict, skip: bool) -> dict:
    if skip:
        return {}
    data: dict = {}
    mode = str(form_value.get("mode") or "").strip().upper()
    if mode:
        data["mode"] = mode
    if str(form_value.get("on_working_days") or "").strip():
        data["on_working_days"] = _as_bool(form_value.get("on_working_days"))
    if str(form_value.get("enable_elastic_robot") or "").strip():
        data["enable_elastic_robot"] = _as_bool(form_value.get("enable_elastic_robot"))

    description = str(form_value.get("description") or "").strip()
    if description:
        data["description"] = description

    for field, label in (
        ("priority", "优先级"),
        ("redial_times", "重拨次数"),
        ("redial_interval", "重拨间隔"),
        ("cs_staff_group_id", "转人工坐席组"),
        ("sms_template_id", "短信模板"),
        ("folder_id", "任务文件夹"),
    ):
        raw = str(form_value.get(field) or "").strip()
        if raw:
            data[field] = _as_int(raw, label)

    for field, label in (
        ("redial_condition", "重拨条件"),
        ("customer_white_group_ids", "客户白名单组"),
    ):
        values = _as_list(form_value.get(field))
        if values:
            data[field] = values
    return data


async def _create_step1(channel, token, form_value, operator, chat_id, message_id) -> None:
    record = _require_record(token, operator, "create")
    if not record:
        await channel.send_text_to_chat(chat_id, "这张表单已过期或已提交，请重新发起创建任务。")
        return
    try:
        params = await asyncio.to_thread(_parse_step1, form_value)
    except ValueError as exc:
        lines = await asyncio.to_thread(aicc_options.phone_lines)
        flows = await asyncio.to_thread(aicc_options.dialog_flows)
        await channel.send_card_to_chat(
            chat_id,
            cards.build_create_form_step1(
                token=token, lines=lines, dialog_flows=flows, error=str(exc)
            ),
        )
        return

    forms.update(token, step="step2", data=params)
    staff, sms, white, folders = await asyncio.gather(
        asyncio.to_thread(aicc_options.staff_groups),
        asyncio.to_thread(aicc_options.sms_templates),
        asyncio.to_thread(aicc_options.white_groups),
        asyncio.to_thread(aicc_options.task_folders),
    )
    await channel.send_card_to_chat(
        chat_id,
        cards.build_create_form_step2(
            token=token,
            params=params,
            staff_groups=staff,
            sms_templates=sms,
            white_groups=white,
            folders=folders,
        ),
    )


async def _create_step2(channel, token, form_value, operator, chat_id, message_id, skip: bool) -> None:
    record = _require_record(token, operator, "create")
    if not record or record.get("step") != "step2":
        await channel.send_text_to_chat(chat_id, "这张表单已过期或已提交，请重新发起创建任务。")
        return
    try:
        extra = _parse_step2(form_value, skip)
    except ValueError as exc:
        params = record.get("data") or {}
        staff, sms, white, folders = await asyncio.gather(
            asyncio.to_thread(aicc_options.staff_groups),
            asyncio.to_thread(aicc_options.sms_templates),
            asyncio.to_thread(aicc_options.white_groups),
            asyncio.to_thread(aicc_options.task_folders),
        )
        await channel.send_card_to_chat(
            chat_id,
            cards.build_create_form_step2(
                token=token, params=params, staff_groups=staff, sms_templates=sms,
                white_groups=white, folders=folders, error=str(exc),
            ),
        )
        return

    params = dict(record.get("data") or {})
    params.update(extra)
    forms.update(token, step="confirm", data=params)

    fields = [
        ("任务名称", params.get("name")),
        ("话术 ID", params.get("dialog_flow_id")),
        ("主叫线路", "、".join(str(item) for item in params.get("phone_number_ids") or [])),
        ("线路类型", params.get("phone_type")),
        ("并发 / BOT", f"{params.get('concurrency_quota')} / {params.get('robot_count')}"),
        ("拨打时段", f"{params.get('daily_start_time')} - {params.get('daily_end_time')}"),
        ("拨打星期", "、".join(str(item) for item in params.get("days_of_week") or [])),
        ("启动方式", params.get("mode", "MANUAL")),
        ("优先级", params.get("priority", 100)),
        ("重拨", f"{params.get('redial_times', 0)} 次 / 间隔 {params.get('redial_interval', 0)} 秒"),
        ("转人工坐席组", params.get("cs_staff_group_id", 0)),
        ("短信模板", params.get("sms_template_id", 0)),
        ("客户白名单组", params.get("customer_white_group_ids") or "无"),
        ("任务描述", params.get("description") or ""),
    ]
    await channel.send_card_to_chat(
        chat_id, cards.build_task_confirm(token=token, kind="create", fields=fields)
    )


async def _create_confirm(channel, token, operator, chat_id) -> None:
    record, reason = forms.pop_for(token, operator)
    if reason != "ok" or not record:
        text = "该确认已过期或已处理，请重新发起创建任务。" if reason != "forbidden" else "这不是你的操作。"
        await channel.send_text_to_chat(chat_id, text)
        return
    params = dict(record.get("data") or {})
    params.pop("phone_type", None)
    log.info("开始创建外呼任务：%s", params.get("name"))
    result = await asyncio.to_thread(aicc_task.create_call_task, **params)
    await channel.send_card_to_chat(
        chat_id,
        cards.build_result(
            "创建任务成功" if result.startswith("✅") else "创建任务失败",
            result,
            ok=result.startswith("✅"),
        ),
    )


async def _start_step1(channel, token, form_value, operator, chat_id, message_id) -> None:
    record = _require_record(token, operator, "start")
    if not record:
        await channel.send_text_to_chat(chat_id, "这张表单已过期，请重新发起启动任务。")
        return

    raw = form_value.get("robot_call_job_id_select") or form_value.get("robot_call_job_id")
    try:
        job_id = _as_int(raw, "任务 ID")
    except ValueError as exc:
        jobs = await asyncio.to_thread(aicc_options.startable_jobs)
        await channel.send_card_to_chat(
            chat_id, cards.build_start_form(token=token, jobs=jobs, error=str(exc))
        )
        return

    try:
        brief = await asyncio.to_thread(aicc_task.job_brief, job_id)
    except Exception as exc:  # noqa: BLE001
        jobs = await asyncio.to_thread(aicc_options.startable_jobs)
        await channel.send_card_to_chat(
            chat_id,
            cards.build_start_form(token=token, jobs=jobs, error=f"读取任务失败：{exc}"),
        )
        return

    forms.update(token, step="confirm", data={"robot_call_job_id": job_id})
    fields = [
        ("任务 ID", brief.get("id")),
        ("任务名称", brief.get("name")),
        ("当前状态", brief.get("status")),
        ("话术", brief.get("dialog_flow_name")),
        ("线路类型", brief.get("phone_type")),
        ("并发 / BOT", f"{brief.get('concurrency')} / {brief.get('robot_count')}"),
        ("拨打时段", brief.get("daily")),
    ]
    await channel.send_card_to_chat(
        chat_id, cards.build_task_confirm(token=token, kind="start", fields=fields)
    )


async def _start_confirm(channel, token, operator, chat_id) -> None:
    record, reason = forms.pop_for(token, operator)
    if reason != "ok" or not record:
        text = "该确认已过期或已处理，请重新发起启动任务。" if reason != "forbidden" else "这不是你的操作。"
        await channel.send_text_to_chat(chat_id, text)
        return
    job_id = int((record.get("data") or {}).get("robot_call_job_id") or 0)
    log.info("开始启动外呼任务：%s", job_id)
    result = await asyncio.to_thread(aicc_task.start_call_task, job_id)
    await channel.send_card_to_chat(
        chat_id,
        cards.build_result(
            "启动指令已提交" if result.startswith("✅") else "启动任务失败",
            result,
            ok=result.startswith("✅"),
        ),
    )
