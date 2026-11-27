"""创建 / 启动外呼任务的卡片流程编排。

交互层（本模块 + 卡片 JSON）负责收集参数、二次确认；
执行层（app.tools.aicc_task）只关心「参数合法就调接口」。
"""

from __future__ import annotations

import asyncio
import logging

from app.cards import cards
from app.clients import aicc_options
from app.core import forms
from app.tools import aicc_task, aicc_task_openapi, aicc_task_status

log = logging.getLogger("aicc_flow")

# 概况接口在缓存不可用时会串行调多个一知接口，必须设上限避免卡片流程挂死
STATUS_OVERVIEW_TIMEOUT = 45.0

# 同一 token + 任务的并发点击只保留一次请求；不同任务按会话串行，
# 避免 Redis 降级时多个几十秒的查询同时压到一知接口、卡片扎堆发出
_OVERVIEW_PENDING: set[str] = set()
_OVERVIEW_CHAT_LOCKS: dict[str, asyncio.Lock] = {}
_MAX_CHAT_LOCKS = 200


def _overview_chat_lock(chat_id: str) -> asyncio.Lock:
    """按会话取锁；超过上限时先回收空闲锁，避免字典无限增长。"""
    lock = _OVERVIEW_CHAT_LOCKS.get(chat_id)
    if lock is None:
        if len(_OVERVIEW_CHAT_LOCKS) >= _MAX_CHAT_LOCKS:
            for key in [
                item for item, value in _OVERVIEW_CHAT_LOCKS.items() if not value.locked()
            ]:
                _OVERVIEW_CHAT_LOCKS.pop(key, None)
        lock = asyncio.Lock()
        _OVERVIEW_CHAT_LOCKS[chat_id] = lock
    return lock


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
    # 话术 BOT 只从 .env 白名单读取，未配置时不发卡，避免用户卡在无法提交的表单上
    bots = aicc_options.dialog_bot_allowlist()
    if not bots:
        await channel.send_text_to_chat(
            chat_id, "未配置话术 BOT（AICC_DIALOG_BOTS），请联系管理员后再创建任务。"
        )
        return

    token = forms.register(
        kind="create",
        step="step1",
        operator=operator,
        chat_id=chat_id,
        message_id=message_id,
    )
    lines = await asyncio.to_thread(aicc_options.phone_lines)
    card = cards.build_create_form_step1(token=token, lines=lines, bots=bots)
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


async def open_pause_form(channel, chat_id: str, operator: str, message_id: str = "") -> None:
    """暂停任务：候选任务走 openapi（只列运行中三类）。"""
    token = forms.register(
        kind="pause",
        step="step1",
        operator=operator,
        chat_id=chat_id,
        message_id=message_id,
    )
    jobs = await asyncio.to_thread(aicc_task_openapi.pausable_jobs)
    card = cards.build_pause_form(token=token, jobs=jobs)
    await channel.send_card_to_chat(chat_id, card)


def _filters_text(filters: dict) -> str:
    status_map = dict(cards.STATUS_FILTER_OPTIONS)
    order_map = dict(cards.ORDER_TYPE_OPTIONS)
    status = status_map.get(str(filters.get("status") or "ALL"), "全部")
    order = order_map.get(str(filters.get("order_type") or "CREATE_TIME"), "-")
    return f"筛选：{status} · {order} · 近 {filters.get('days', 15)} 天"


def _parse_status_filters(form_value: dict) -> dict:
    status = str(form_value.get("status_filter") or "ALL")
    if status not in dict(cards.STATUS_FILTER_OPTIONS):
        status = "ALL"
    order = str(form_value.get("order_type") or "CREATE_TIME")
    if order not in dict(cards.ORDER_TYPE_OPTIONS):
        order = "CREATE_TIME"
    raw_days = str(form_value.get("days") or "15").strip()
    days = int(raw_days) if raw_days.isdigit() else 15
    if days not in (7, 15, 30):
        days = 15
    return {"status": status, "order_type": order, "days": days}


async def open_status_form(
    channel,
    chat_id: str,
    operator: str,
    message_id: str = "",
    keyword: str = "",
) -> None:
    """任务状态查询入口：有关键词先匹配，否则直接给筛选卡。"""
    filters = {"status": "ALL", "order_type": "CREATE_TIME", "days": 15}
    token = forms.register(
        kind="status",
        step="filter",
        operator=operator,
        chat_id=chat_id,
        message_id=message_id,
        data={"filters": filters},
    )
    keyword = (keyword or "").strip()
    if not keyword:
        await channel.send_card_to_chat(chat_id, cards.build_status_filter(token=token))
        return

    try:
        matches = await asyncio.to_thread(aicc_task_status.find_jobs_by_keyword, keyword)
    except Exception as exc:  # noqa: BLE001
        log.warning("按关键词匹配任务失败：%s", exc)
        await channel.send_card_to_chat(
            chat_id, cards.build_status_filter(token=token, error=f"查询任务失败：{exc}")
        )
        return

    if len(matches) == 1:
        await _send_status_overview(channel, chat_id, token, matches[0]["id"], filters)
        return
    if matches:
        forms.update(
            token,
            step="list",
            data={"filters": filters, "jobs": matches, "page": 1, "keyword": keyword},
        )
        await channel.send_card_to_chat(
            chat_id,
            cards.build_status_list(
                token=token,
                jobs=matches,
                page=1,
                filters_text=f"{_filters_text(filters)}\n匹配关键词：{keyword}",
            ),
        )
        return
    await channel.send_card_to_chat(
        chat_id, cards.build_status_filter(token=token, error=f"未找到包含「{keyword}」的任务")
    )


async def _send_status_overview(
    channel,
    chat_id: str,
    token: str,
    job_id: int,
    filters: dict,
    force: bool = False,
) -> None:
    try:
        overview = await asyncio.wait_for(
            asyncio.to_thread(aicc_task_status.build_overview, job_id, force),
            timeout=STATUS_OVERVIEW_TIMEOUT,
        )
    except asyncio.TimeoutError:
        await channel.send_text_to_chat(
            chat_id,
            f"读取任务 {job_id} 的运行概况超时，请稍后重试。",
        )
        return
    except Exception as exc:  # noqa: BLE001
        await channel.send_text_to_chat(chat_id, f"读取任务 {job_id} 的运行概况失败：{exc}")
        return
    record, _reason = forms.get_for(token)
    data = dict((record or {}).get("data") or {})
    data.update({"job_id": int(job_id), "filters": filters})
    forms.update(token, step="detail", data=data)
    await channel.send_card_to_chat(
        chat_id, cards.build_status_overview(token=token, overview=overview)
    )


async def _status_query(channel, token: str, form_value: dict, operator: str, chat_id: str) -> None:
    record = _require_record(token, operator, "status")
    if not record:
        await channel.send_text_to_chat(chat_id, "这张卡片已过期或已提交，请重新发起任务状态查询。")
        return
    filters = _parse_status_filters(form_value)
    try:
        jobs = await asyncio.to_thread(
            aicc_task_status.query_jobs,
            status=filters["status"],
            order_type=filters["order_type"],
            days=filters["days"],
        )
    except Exception as exc:  # noqa: BLE001
        await channel.send_card_to_chat(
            chat_id, cards.build_status_filter(token=token, error=f"查询任务失败：{exc}")
        )
        return

    if not jobs:
        await channel.send_card_to_chat(
            chat_id,
            cards.build_status_filter(
                token=token, error=f"{_filters_text(filters)}：没有符合条件的任务"
            ),
        )
        return

    forms.update(token, step="list", data={"filters": filters, "jobs": jobs, "page": 1})
    await channel.send_card_to_chat(
        chat_id,
        cards.build_status_list(
            token=token, jobs=jobs, page=1, filters_text=_filters_text(filters)
        ),
    )


async def _status_page(channel, token: str, value: dict, operator: str, chat_id: str) -> None:
    record = _require_record(token, operator, "status")
    if not record or record.get("step") != "list":
        await channel.send_text_to_chat(chat_id, "任务列表已过期，请重新筛选。")
        return
    jobs = record.get("data", {}).get("jobs") or []
    filters = record.get("data", {}).get("filters") or {}
    page = str(value.get("page") or "1")
    page = int(page) if page.isdigit() else 1
    data = dict(record.get("data") or {})
    data["page"] = page
    forms.update(token, data=data)
    await channel.send_card_to_chat(
        chat_id,
        cards.build_status_list(
            token=token, jobs=jobs, page=page, filters_text=_filters_text(filters)
        ),
    )


async def _status_back(channel, token: str, value: dict, operator: str, chat_id: str) -> None:
    record = _require_record(token, operator, "status")
    if not record:
        await channel.send_text_to_chat(chat_id, "这张卡片已过期，请重新发起任务状态查询。")
        return
    target = str(value.get("to") or "list")
    data = record.get("data") or {}
    jobs = data.get("jobs") or []
    if target == "filter" or not jobs:
        await channel.send_card_to_chat(chat_id, cards.build_status_filter(token=token))
        return
    filters = data.get("filters") or {}
    page = data.get("page") or 1
    await channel.send_card_to_chat(
        chat_id,
        cards.build_status_list(
            token=token, jobs=jobs, page=page, filters_text=_filters_text(filters)
        ),
    )


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
        if action in ("debug_open_pause", "open_pause"):
            await open_pause_form(channel, chat_id, operator, message_id)
            return

        if action == "task_form_refresh":
            kind = str(value.get("kind") or "create")
            if kind == "start":
                await open_start_form(channel, chat_id, operator, message_id)
            elif kind == "pause":
                await open_pause_form(channel, chat_id, operator, message_id)
            else:
                await open_create_form(channel, chat_id, operator, message_id)
            return

        if action in ("debug_open_status", "open_status"):
            await open_status_form(
                channel,
                chat_id,
                operator,
                message_id,
                str(value.get("task_keyword") or ""),
            )
            return
        if action == "task_status_filter":
            await open_status_form(channel, chat_id, operator, message_id)
            return
        if action == "task_status_query":
            await _status_query(channel, token, form_value, operator, chat_id)
            return
        if action == "task_status_page":
            await _status_page(channel, token, value, operator, chat_id)
            return
        if action in ("task_status_pick", "task_status_refresh"):
            # 卡片级一次性闸门：一张卡只认第一次点击，连点/重投全部丢弃。
            # 放在所有校验之前，保证「宣布失效」不依赖任何卡片更新接口是否成功。
            if message_id and not forms.consume_card_action(message_id):
                log.info("卡片重复点击已丢弃 action=%s message=%s", action, message_id)
                return
            record = _require_record(token, operator, "status")
            if not record:
                await channel.send_text_to_chat(chat_id, "这张卡片已过期，请重新发起任务状态查询。")
                return
            raw_job_id = str(value.get("job_id") or "")
            if not raw_job_id.isdigit():
                await channel.send_text_to_chat(chat_id, "任务 ID 无效，请重新选择任务。")
                return
            filters = record.get("data", {}).get("filters") or {}
            job_id = int(raw_job_id)
            pending_key = f"{token}:{job_id}"
            if pending_key in _OVERVIEW_PENDING:
                log.info("任务 %s 概况正在读取，忽略重复点击", job_id)
                return
            _OVERVIEW_PENDING.add(pending_key)
            try:
                # 同一会话的概况请求串行执行，避免多张卡同时返回
                async with _overview_chat_lock(chat_id):
                    await _send_status_overview(
                        channel,
                        chat_id,
                        token,
                        job_id,
                        filters,
                        force=action == "task_status_refresh",
                    )
            finally:
                _OVERVIEW_PENDING.discard(pending_key)
            return
        if action == "task_status_back":
            await _status_back(channel, token, value, operator, chat_id)
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

        if action == "task_pause_step1":
            await _pause_step1(channel, token, form_value, operator, chat_id, message_id)
            return
        if action == "task_pause_confirm":
            await _pause_confirm(channel, token, operator, chat_id)
            return

        log.warning("未知的卡片动作：%s", action)
    except Exception:  # noqa: BLE001
        log.exception("处理卡片动作失败 action=%s", action)
        try:
            await channel.send_text_to_chat(chat_id, "处理失败，请稍后重试；若持续失败请联系管理员。")
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
    bots = {bot["id"]: bot["name"] for bot in aicc_options.dialog_bot_allowlist()}
    if dialog_flow_id not in bots:
        raise ValueError("话术 / BOT 不在允许列表中，请重新选择")
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
    lines_by_type = aicc_task.load_active_lines()
    phone_type = aicc_task.line_type_of(ids, lines_by_type)
    _usable, limit = aicc_task.check_lines(ids, phone_type, lines_by_type)
    if limit and concurrency > limit:
        raise ValueError(f"并发数 {concurrency} 超过线路并发上限 {limit}")

    return {
        "name": name,
        "dialog_flow_id": dialog_flow_id,
        "dialog_flow_name": bots[dialog_flow_id],
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
        bots = aicc_options.dialog_bot_allowlist()
        if not bots:
            await channel.send_text_to_chat(
                chat_id, "话术 BOT 配置已变更，请重新发起创建任务。"
            )
            return
        lines = await asyncio.to_thread(aicc_options.phone_lines)
        await channel.send_card_to_chat(
            chat_id,
            cards.build_create_form_step1(
                token=token, lines=lines, bots=bots, error=str(exc)
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
        (
            "话术 BOT",
            f"{params.get('dialog_flow_name') or '-'}（ID {params.get('dialog_flow_id')}）",
        ),
        ("主叫线路", "、".join(str(item) for item in params.get("phone_number_ids") or [])),
        ("线路类型", params.get("phone_type")),
        ("并发 / BOT", f"{params.get('concurrency_quota')} / {params.get('robot_count')}"),
        ("拨打时段", f"{params.get('daily_start_time')} - {params.get('daily_end_time')}"),
        ("拨打星期", "、".join(str(item) for item in params.get("days_of_week") or [])),
        ("启动方式", params.get("mode", "MANUAL")),
        ("优先级", params.get("priority", 100)),
        ("重拨", f"{params.get('redial_times', 0)} 次 / 间隔 {params.get('redial_interval', 0)} 分钟"),
        ("转人工坐席组", params.get("cs_staff_group_id", 0)),
        ("短信模板", params.get("sms_template_id", 0)),
        ("客户白名单组", params.get("customer_white_group_ids") or "无"),
        ("任务描述", params.get("description") or ""),
    ]
    hint = aicc_task.LINE_TYPE_HINTS.get(str(params.get("phone_type") or ""))
    if hint:
        fields.append(("线路类型提示", hint))
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
    # 展示用名称不参与一知接口调用
    params.pop("dialog_flow_name", None)
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


async def _pause_step1(channel, token, form_value, operator, chat_id, message_id) -> None:
    record = _require_record(token, operator, "pause")
    if not record:
        await channel.send_text_to_chat(chat_id, "这张表单已过期，请重新发起暂停任务。")
        return

    raw = form_value.get("robot_call_job_id_select") or form_value.get("robot_call_job_id")
    try:
        job_id = _as_int(raw, "任务 ID")
    except ValueError as exc:
        jobs = await asyncio.to_thread(aicc_task_openapi.pausable_jobs)
        await channel.send_card_to_chat(
            chat_id, cards.build_pause_form(token=token, jobs=jobs, error=str(exc))
        )
        return

    try:
        brief = await asyncio.to_thread(aicc_task_openapi.job_brief, job_id)
    except Exception as exc:  # noqa: BLE001
        jobs = await asyncio.to_thread(aicc_task_openapi.pausable_jobs)
        await channel.send_card_to_chat(
            chat_id,
            cards.build_pause_form(token=token, jobs=jobs, error=f"读取任务失败：{exc}"),
        )
        return

    forms.update(token, step="confirm", data={"robot_call_job_id": job_id})
    fields = [
        ("任务 ID", brief.get("id")),
        ("任务名称", brief.get("name")),
        ("当前状态", brief.get("status_label") or brief.get("status")),
        ("话术", brief.get("dialog_flow_name")),
        ("线路类型", brief.get("phone_type")),
        ("并发 / BOT", f"{brief.get('concurrency')} / {brief.get('robot_count')}"),
        ("拨打时段", brief.get("daily")),
    ]
    await channel.send_card_to_chat(
        chat_id, cards.build_task_confirm(token=token, kind="pause", fields=fields)
    )


async def _pause_confirm(channel, token, operator, chat_id) -> None:
    record, reason = forms.pop_for(token, operator)
    if reason != "ok" or not record:
        text = "该确认已过期或已处理，请重新发起暂停任务。" if reason != "forbidden" else "这不是你的操作。"
        await channel.send_text_to_chat(chat_id, text)
        return
    job_id = int((record.get("data") or {}).get("robot_call_job_id") or 0)
    log.info("开始暂停外呼任务：%s", job_id)
    result = await asyncio.to_thread(aicc_task_openapi.pause_task, job_id)
    await channel.send_card_to_chat(
        chat_id,
        cards.build_result(
            "暂停指令已提交" if result.startswith("✅") else "暂停任务失败",
            result,
            ok=result.startswith("✅"),
        ),
    )
