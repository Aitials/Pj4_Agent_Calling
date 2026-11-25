"""钉钉任务卡片构造（阶段 4.3：卡片模块骨架）。

只负责拼「通道中立的卡片描述」，不做接口调用与流程判断：
- 表单卡：`kind="form"`，模板表单字段 + 隐藏 token/action；
- 按钮/结果卡：`kind="buttons"` / `kind="markdown"`。

函数名与飞书模块保持一致，流程层通过 `app.cards.cards` 代理调用。
"""

from __future__ import annotations

from typing import Any

# 纯数据常量直接复用飞书模块，避免两套选项漂移
from app.cards.aicc_task_cards import (  # noqa: F401
    DAY_LABELS,
    DAYS_OPTIONS,
    ORDER_TYPE_OPTIONS,
    REDIAL_LABELS,
    STATUS_FILTER_OPTIONS,
)

# 表单模板字段类型（与官方「表单变量」协议一致）
TEXT = "TEXT"
TEXT_AREA = "TEXT_AREA"
NUMBER = "NUMBER"
SELECT = "SELECT"
MULTI_SELECT = "MULTI_SELECT"


def _option(label: Any, value: Any) -> dict:
    return {"text": str(label), "value": str(value)}


def _line_options(lines: dict[str, list[dict]]) -> list[dict]:
    options: list[dict] = []
    for phone_type, rows in (lines or {}).items():
        for line in rows or []:
            line_id = line.get("tenantPhoneNumberId")
            if not line_id:
                continue
            number = line.get("phoneNumber") or line_id
            options.append(_option(f"{number}（{phone_type}）", line_id))
    return options


def _named_options(rows: list[dict], zero_label: str = "") -> list[dict]:
    options = [_option(zero_label, 0)] if zero_label else []
    for row in rows or []:
        options.append(_option(row.get("name") or row.get("id"), row.get("id")))
    return options


def _text(
    name: str,
    label: str,
    *,
    required: bool = False,
    default: str = "",
    placeholder: str = "",
    read_only: bool = False,
) -> dict:
    field = {
        "name": name,
        "label": label,
        "type": TEXT,
        "required": required,
        "readOnly": read_only,
    }
    if default:
        field["defaultValue"] = default
    if placeholder:
        field["placeholder"] = placeholder
    return field


def _text_area(
    name: str,
    label: str,
    *,
    default: str = "",
    read_only: bool = False,
    placeholder: str = "",
) -> dict:
    field = {
        "name": name,
        "label": label,
        "type": TEXT_AREA,
        "readOnly": read_only,
    }
    if default:
        field["defaultValue"] = default
    if placeholder:
        field["placeholder"] = placeholder
    return field


def _number(
    name: str, label: str, *, default: int | float = 0, required: bool = False
) -> dict:
    return {
        "name": name,
        "label": label,
        "type": NUMBER,
        "required": required,
        "defaultValue": default,
    }


def _select(
    name: str,
    label: str,
    options: list[dict],
    *,
    required: bool = False,
    placeholder: str = "",
) -> dict:
    field = {
        "name": name,
        "label": label,
        "type": SELECT,
        "required": required,
        "options": options,
    }
    if placeholder:
        field["placeholder"] = placeholder
    return field


def _multi_select(
    name: str,
    label: str,
    options: list[dict],
    *,
    required: bool = False,
    placeholder: str = "",
) -> dict:
    field = {
        "name": name,
        "label": label,
        "type": MULTI_SELECT,
        "required": required,
        "options": options,
    }
    if placeholder:
        field["placeholder"] = placeholder
    return field


def _error_field(error: str) -> dict:
    return {
        "name": "__error",
        "label": "⚠️ 提交参数不合法",
        "type": TEXT,
        "readOnly": True,
        "defaultValue": error,
    }


def _form_card(
    title: str,
    *,
    token: str,
    action: str,
    fields: list[dict],
    btn_text: str,
    error: str = "",
) -> dict:
    all_fields = [_error_field(error)] if error else []
    all_fields.extend(fields)
    return {
        "kind": "form",
        "title": title,
        "fields": all_fields,
        "hidden": {"token": token, "__action": action},
        "btn_text": btn_text,
    }


def build_create_form_step1(
    *,
    token: str,
    lines: dict[str, list[dict]],
    bots: list[dict],
    error: str = "",
) -> dict:
    """创建任务第一步：必填项。"""
    line_options = _line_options(lines)
    bot_options = [
        _option(bot.get("name") or bot.get("id"), bot.get("id")) for bot in bots or []
    ]
    fields = [
        _text(
            "name",
            "任务名称",
            required=True,
            placeholder="例如：11月新客回访",
        ),
        _select(
            "dialog_flow_id",
            "话术 / BOT",
            bot_options,
            required=True,
            placeholder="请选择话术",
        ),
        (
            _multi_select(
                "phone_number_ids",
                "主叫线路（可多选）",
                line_options,
                required=True,
            )
            if line_options
            else _text(
                "phone_number_ids",
                "主叫线路 ID（逗号分隔）",
                required=True,
                placeholder="选项加载失败，请手填线路 ID",
            )
        ),
        _number("concurrency_quota", "线路并发数", default=1, required=True),
        _number("robot_count", "BOT 数量", default=1, required=True),
        _text(
            "daily_start_time",
            "每日开始时间",
            required=True,
            default="09:00:00",
            placeholder="HH:MM:SS",
        ),
        _text(
            "daily_end_time",
            "每日结束时间",
            required=True,
            default="18:00:00",
            placeholder="HH:MM:SS",
        ),
        _multi_select(
            "days_of_week",
            "拨打星期（可多选）",
            [_option(label, value) for value, label in DAY_LABELS],
            required=True,
        ),
    ]
    return _form_card(
        "创建外呼任务 · 第 1 步（必填）",
        token=token,
        action="task_create_step1",
        fields=fields,
        btn_text="下一步",
        error=error,
    )


def _step1_summary(params: dict) -> str:
    return "\n".join(
        [
            f"任务名：{params.get('name') or '-'}",
            f"话术：{params.get('dialog_flow_name') or '-'}"
            f"（ID {params.get('dialog_flow_id')}）",
            f"线路：{'、'.join(str(item) for item in params.get('phone_number_ids') or [])}",
            f"并发 / BOT：{params.get('concurrency_quota')} / {params.get('robot_count')}",
            f"时段：{params.get('daily_start_time')} - {params.get('daily_end_time')}",
            f"星期：{'、'.join(str(item) for item in params.get('days_of_week') or [])}",
        ]
    )


def build_create_form_step2(
    *,
    token: str,
    params: dict,
    staff_groups: list[dict],
    sms_templates: list[dict],
    white_groups: list[dict],
    folders: list[dict],
    error: str = "",
) -> dict:
    """创建任务第二步：选填项，留空取默认值。"""
    staff_options = _named_options(staff_groups, "不转人工")
    sms_options = _named_options(sms_templates, "不发送短信")
    folder_options = _named_options(folders, "不放入文件夹")
    white_options = _named_options(white_groups)

    fields = [
        _text_area(
            "__summary",
            "第 1 步参数",
            default=_step1_summary(params),
            read_only=True,
        ),
        _select(
            "mode",
            "启动方式",
            [_option("手动启动（MANUAL）", "MANUAL"), _option("自动启动（AUTO）", "AUTO")],
            placeholder="默认手动",
        ),
        _number("priority", "优先级", default=100),
        _select(
            "on_working_days",
            "仅法定工作日",
            [_option("否", "false"), _option("是", "true")],
            placeholder="默认否",
        ),
        _text_area("description", "任务描述", placeholder="可不填"),
        _select("folder_id", "任务文件夹", folder_options),
        _number("redial_times", "重拨次数", default=0),
        _number("redial_interval", "重拨间隔（分钟）", default=0),
        _multi_select(
            "redial_condition",
            "重拨条件（可多选）",
            [_option(label, value) for value, label in REDIAL_LABELS],
            placeholder="不选则不限",
        ),
        _select("cs_staff_group_id", "转人工坐席组", staff_options),
        _select("sms_template_id", "短信模板", sms_options),
        (
            _multi_select(
                "customer_white_group_ids",
                "客户白名单组（可多选）",
                white_options,
            )
            if white_options
            else _text(
                "customer_white_group_ids",
                "客户白名单组 ID（逗号分隔）",
                placeholder="可不填",
            )
        ),
        _select(
            "enable_elastic_robot",
            "弹性 BOT",
            [_option("否", "false"), _option("是", "true")],
            placeholder="默认否",
        ),
    ]
    return _form_card(
        "创建外呼任务 · 第 2 步（选填）",
        token=token,
        action="task_create_step2",
        fields=fields,
        btn_text="下一步（留空取默认值）",
        error=error,
    )


def build_start_form(*, token: str, jobs: list[dict], error: str = "") -> dict:
    """启动任务：下拉选择 + 手填 ID 兜底。"""
    job_options = [
        _option(f"{row.get('name')}（{row.get('status')}）", row.get("id"))
        for row in jobs or []
        if row.get("id") is not None
    ]
    fields: list[dict] = []
    if job_options:
        fields.append(
            _select(
                "robot_call_job_id_select",
                "选择任务",
                job_options,
                placeholder="请选择要启动的任务",
            )
        )
    fields.append(
        _text(
            "robot_call_job_id",
            "或手填任务 ID",
            placeholder="选项里没有时可手填任务 ID",
        )
    )
    return _form_card(
        "启动外呼任务",
        token=token,
        action="task_start_step1",
        fields=fields,
        btn_text="下一步",
        error=error,
    )


def build_task_confirm(
    *, token: str, kind: str, fields: list[tuple[str, Any]]
) -> dict:
    """二次确认卡：只读摘要 + 确认/取消按钮。"""
    lines = ["确认后才会真正提交到一知平台：", ""]
    for label, value in fields:
        lines.append(f"- **{label}**：{value if value not in (None, '') else '-'}")
    title = "确认创建外呼任务" if kind == "create" else "确认启动外呼任务"
    action = "task_create_confirm" if kind == "create" else "task_start_confirm"
    return {
        "kind": "buttons",
        "title": title,
        "markdown": "\n".join(lines),
        "buttons": [
            {
                "text": "取消",
                "id": f"task_form_cancel|{token}",
                "color": "blue",
            },
            {
                "text": "确认执行",
                "id": f"{action}|{token}",
                "color": "blue",
            },
        ],
    }


def build_result(title: str, content: str, ok: bool = True) -> dict:
    return {
        "kind": "markdown",
        "title": title or ("处理结果" if ok else "处理失败"),
        "markdown": content or "",
    }


def build_processing(text: str = "正在处理，请稍候…") -> dict:
    return {"kind": "markdown", "title": "处理中", "markdown": text}


# ---------------------------------------------------------------------------
# 任务运行概况：筛选 → 任务列表 → 概况
# ---------------------------------------------------------------------------


def build_status_filter(*, token: str, error: str = "") -> dict:
    """任务状态查询入口：只做筛选。"""
    fields = [
        _select(
            "status_filter",
            "任务状态",
            [_option(label, value) for value, label in STATUS_FILTER_OPTIONS],
            placeholder="默认全部",
        ),
        _select(
            "order_type",
            "排序方式",
            [_option(label, value) for value, label in ORDER_TYPE_OPTIONS],
            placeholder="默认按创建时间",
        ),
        _select(
            "days",
            "创建时间范围",
            [_option(label, value) for value, label in DAYS_OPTIONS],
            placeholder="默认近 15 天",
        ),
    ]
    return _form_card(
        "任务运行概况 · 选择筛选条件",
        token=token,
        action="task_status_query",
        fields=fields,
        btn_text="查询任务",
        error=error,
    )


def build_status_list(
    *,
    token: str,
    jobs: list[dict],
    page: int,
    page_size: int = 10,
    filters_text: str = "",
) -> dict:
    """任务列表卡：每页最多 5 条，每条一个「查看」按钮。"""
    total = len(jobs or [])
    # AI 卡片按钮数量保守控制：5 条任务 + 上一页/下一页 = 最多 7 个
    size = min(max(int(page_size or 5), 1), 5)
    pages = max((total + size - 1) // size, 1)
    page = min(max(int(page or 1), 1), pages)
    start = (page - 1) * size
    rows = (jobs or [])[start : start + size]

    lines: list[str] = []
    if filters_text:
        lines.append(filters_text)
    lines.append(f"共 **{total}** 个任务，第 {page} / {pages} 页")

    buttons: list[dict] = []
    for job in rows:
        lines.append("")
        lines.append(
            f"**{job.get('name') or '-'}**"
            f"（{job.get('status_label') or job.get('status') or '-'}）"
        )
        lines.append(f"创建于 {job.get('create_time') or '-'} · ID {job.get('id')}")
        if job.get("dialog_flow_name"):
            lines.append(f"话术：{job['dialog_flow_name']}")
        buttons.append(
            {
                "text": f"查看 #{job.get('id')}",
                "id": f"task_status_pick|{token}|{job.get('id')}",
                "color": "blue",
            }
        )

    if pages > 1:
        buttons.append(
            {
                "text": "上一页",
                "id": f"task_status_page|{token}|{page - 1}",
                "color": "blue",
            }
        )
        buttons.append(
            {
                "text": "下一页",
                "id": f"task_status_page|{token}|{page + 1}",
                "color": "blue",
            }
        )

    return {
        "kind": "buttons",
        "title": "任务运行概况 · 选择任务",
        "markdown": "\n".join(lines),
        "buttons": buttons,
    }


def build_status_overview(*, token: str, overview: dict) -> dict:
    """运行概况卡：状态与进度 / 通话效果 / 意向分布 / 配置与提示。"""
    progress = overview.get("progress") or {}
    call = overview.get("call") or {}
    config = overview.get("config") or {}
    hints = overview.get("hints") or []

    state = f"**状态：{overview.get('status_label')}（{overview.get('status')}）**"
    if overview.get("ended"):
        state += " · 已结束"
    lines = [state, ""]

    lines.append("**状态与进度**")
    lines.append(
        f"- 客户进度：{progress.get('completed', 0)} / {progress.get('total', 0)}"
        f"（{float(progress.get('percent') or 0):.1f}%）"
    )
    lines.append(f"- 已过滤：{progress.get('filtered', 0)} 个")
    if config.get("next_start"):
        lines.append(f"- 下次启动：{config['next_start']}")
    lines.append(f"- 最近外呼：{call.get('last_call_date') or '-'}")

    lines.extend(
        [
            "",
            "**通话效果**",
            f"- 呼叫量：{call.get('total', 0)} 通",
            f"- 接通量：{call.get('answered', 0)} 通"
            f"，接通率 {float(call.get('rate') or 0):.2f}%",
            f"- 平均通话时长：{float(call.get('avg_duration') or 0):.0f} 秒",
            f"- 客户挂断率：{float(call.get('hangup_rate') or 0):.2f}%"
            f" · A/B 类占比：{float(call.get('ab_ratio') or 0):.2f}%",
        ]
    )

    intent_lines = ["", "**意向分布**"]
    intents = overview.get("intents") or []
    if intents:
        for item in intents:
            intent_lines.append(
                f"- {item.get('name')}：{item.get('count')} 个"
                f"（{float(item.get('share') or 0):.1f}%）"
            )
    else:
        intent_lines.append("- 暂无已分类意向")
    lines.extend(intent_lines)

    lines.extend(
        [
            "",
            "**配置摘要**",
            f"- 话术：{config.get('dialog_flow') or '-'}",
            f"- 线路：{config.get('lines') or '-'}",
            f"- 并发 / BOT：{config.get('concurrency', 0)} / {config.get('robot_count', 0)}",
            f"- 拨打时段：{config.get('daily') or '-'}（{config.get('days') or '-'}）",
            f"- 优先级：{config.get('priority', 0)}"
            f" · 创建人：{config.get('created_by') or '-'}",
        ]
    )

    if hints:
        lines.extend(["", "**异常提示**"])
        lines.extend(f"- {item}" for item in hints)

    lines.extend(
        ["", f"数据时间 {overview.get('fetched_at') or '-'}（缓存不超过 60 秒）"]
    )

    buttons = [
        {
            "text": "刷新数据",
            "id": f"task_status_refresh|{token}|{overview.get('job_id')}",
            "color": "blue",
        },
        {
            "text": "返回列表",
            "id": f"task_status_back|{token}|list",
            "color": "blue",
        },
        {
            "text": "重新筛选",
            "id": f"task_status_filter|{token}",
            "color": "blue",
        },
        {
            "text": "关闭",
            "id": f"task_form_cancel|{token}",
            "color": "blue",
        },
    ]
    return {
        "kind": "buttons",
        "title": f"{overview.get('name') or '-'} · 运行概况",
        "markdown": "\n".join(lines),
        "buttons": buttons,
    }
