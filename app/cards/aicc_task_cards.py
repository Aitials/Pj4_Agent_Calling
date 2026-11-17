"""一知外呼任务卡片构造（Card JSON 2.0）。

只负责拼 JSON，不做接口调用与流程判断。
表单字段的选项由调用方在发卡前实时拉取后传入；传空时自动退化为输入框。
"""

from __future__ import annotations

from typing import Any

DAY_LABELS = [
    ("MONDAY", "周一"),
    ("TUESDAY", "周二"),
    ("WEDNESDAY", "周三"),
    ("THURSDAY", "周四"),
    ("FRIDAY", "周五"),
    ("SATURDAY", "周六"),
    ("SUNDAY", "周日"),
]

REDIAL_LABELS = [
    ("CALL_LOSS", "呼叫失败"),
    ("NO_ANSWER", "无人接听"),
    ("BUSY", "占线"),
    ("REFUSED", "拒接"),
    ("POWER_OFF", "关机"),
    ("OUT_OF_SERVICE", "停机"),
    ("CAN_NOT_CONNECT", "无法接通"),
    ("FROM_PHONE_ERROR", "主叫错误"),
    ("SYSTEM_ERROR", "系统错误"),
]


def _text(content: str) -> dict:
    return {"tag": "plain_text", "content": content}


def _markdown(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _option(label: str, value: Any) -> dict:
    return {"text": _text(label), "value": str(value)}


def _input(
    name: str,
    label: str,
    *,
    required: bool = False,
    default: str = "",
    placeholder: str = "",
    max_length: int = 200,
) -> dict:
    element: dict[str, Any] = {
        "tag": "input",
        "name": name,
        "required": required,
        "label": _text(label),
        "label_position": "top",
        "placeholder": _text(placeholder or f"请输入{label}"),
        "max_length": max_length,
    }
    if default:
        element["default_value"] = default
    return element


def _select(
    name: str,
    label: str,
    options: list[dict],
    *,
    required: bool = False,
    multiple: bool = False,
    placeholder: str = "请选择",
) -> dict:
    # 注意：select_static / multi_select_static 不支持 label、label_position，
    # 传了会被飞书判为 unknown property（200621），所以字段名放进 placeholder。
    return {
        "tag": "multi_select_static" if multiple else "select_static",
        "name": name,
        "required": required,
        "placeholder": _text(f"{label}：{placeholder}"),
        "type": "default",
        "options": options,
    }


def _submit_button(label: str, action: str, value: dict | None = None) -> dict:
    payload = {"action": action}
    payload.update(value or {})
    return {
        "tag": "button",
        "name": action,
        "text": _text(label),
        "type": "primary",
        "width": "fill",
        "form_action_type": "submit",
        "behaviors": [{"type": "callback", "value": payload}],
    }


def _callback_button(label: str, action: str, value: dict | None = None, type_: str = "default") -> dict:
    payload = {"action": action}
    payload.update(value or {})
    return {
        "tag": "button",
        "name": action,
        "text": _text(label),
        "type": type_,
        "width": "fill",
        "behaviors": [{"type": "callback", "value": payload}],
    }


def _columns(*buttons: dict) -> dict:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "default",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "elements": [button]}
            for button in buttons
        ],
    }


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


def _named_options(rows: list[dict], with_status: bool = True) -> list[dict]:
    options: list[dict] = []
    for row in rows or []:
        label = str(row.get("name") or row.get("id"))
        status = str(row.get("status") or "")
        if with_status and status:
            label = f"{label}（{status}）"
        options.append(_option(label, row.get("id")))
    return options


def _base_card(title: str, elements: list[dict], template: str = "blue") -> dict:
    return {
        "schema": "2.0",
        "header": {"title": _text(title), "template": template},
        "body": {"elements": elements},
    }


def _error_line(error: str) -> dict:
    return _markdown(f"**⚠️ 提交参数不合法：{error}**\n请修改后重新提交，或点「取消」退出。")


def build_create_form_step1(
    *,
    token: str,
    lines: dict[str, list[dict]],
    dialog_flows: list[dict],
    error: str = "",
) -> dict:
    """创建任务第一步：必填项。"""
    elements: list[dict] = []
    if error:
        elements.append(_error_line(error))

    line_options = _line_options(lines)
    flow_options = _named_options(dialog_flows, with_status=True)

    form_elements: list[dict] = [
        _input("name", "任务名称", required=True, placeholder="例如：11月新客回访", max_length=50),
        (
            _select("dialog_flow_id", "话术 / BOT", flow_options, required=True, placeholder="请选择话术")
            if flow_options
            else _input("dialog_flow_id", "话术 / BOT ID", required=True, placeholder="选项加载失败，请手填话术 ID")
        ),
        (
            _select("phone_number_ids", "主叫线路（可多选）", line_options, required=True, multiple=True)
            if line_options
            else _input("phone_number_ids", "主叫线路 ID（逗号分隔）", required=True, placeholder="选项加载失败，请手填线路 ID")
        ),
        _input("concurrency_quota", "线路并发数", required=True, default="1", placeholder="例如：1"),
        _input("robot_count", "BOT 数量", required=True, default="1", placeholder="例如：1"),
        _input("daily_start_time", "每日开始时间", required=True, default="09:00:00", placeholder="HH:MM:SS"),
        _input("daily_end_time", "每日结束时间", required=True, default="18:00:00", placeholder="HH:MM:SS"),
        _select(
            "days_of_week",
            "拨打星期（可多选）",
            [_option(label, value) for value, label in DAY_LABELS],
            required=True,
            multiple=True,
        ),
        _submit_button("下一步", "task_create_step1", {"token": token}),
    ]

    elements.append(
        {
            "tag": "form",
            "name": "create_task_step1",
            "direction": "vertical",
            "vertical_spacing": "8px",
            "elements": form_elements,
        }
    )
    elements.append(
        _columns(
            _callback_button("刷新选项", "task_form_refresh", {"kind": "create"}),
            _callback_button("取消", "task_form_cancel", {"token": token}),
        )
    )
    return _base_card("创建外呼任务 · 第 1 步（必填）", elements)


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
    elements: list[dict] = []
    if error:
        elements.append(_error_line(error))

    elements.append(
        _markdown(
            "第 1 步参数已校验通过：\n"
            f"- 任务名：{params.get('name')}\n"
            f"- 话术 ID：{params.get('dialog_flow_id')}\n"
            f"- 线路：{'、'.join(str(item) for item in params.get('phone_number_ids') or [])}\n"
            f"- 并发 / BOT：{params.get('concurrency_quota')} / {params.get('robot_count')}\n"
            f"- 时段：{params.get('daily_start_time')} - {params.get('daily_end_time')}\n"
            f"- 星期：{'、'.join(str(item) for item in params.get('days_of_week') or [])}"
        )
    )

    staff_options = [_option("不转人工", 0)] + _named_options(staff_groups, with_status=False)
    sms_options = [_option("不发送短信", 0)] + _named_options(sms_templates, with_status=False)
    folder_options = [_option("不放入文件夹", 0)] + [
        _option(str(row.get("name")), row.get("id")) for row in folders or []
    ]
    white_options = _named_options(white_groups, with_status=False)

    form_elements: list[dict] = [
        _select("mode", "启动方式", [_option("手动启动（MANUAL）", "MANUAL"), _option("自动启动（AUTO）", "AUTO")]),
        _input("priority", "优先级", default="100", placeholder="默认 100"),
        _select("on_working_days", "仅法定工作日", [_option("否", "false"), _option("是", "true")]),
        _input("description", "任务描述", placeholder="可不填", max_length=500),
        _select("folder_id", "任务文件夹", folder_options if folders else [_option("不放入文件夹", 0)]),
        _input("redial_times", "重拨次数", default="0", placeholder="默认 0（不重拨）"),
        _input("redial_interval", "重拨间隔（秒）", default="0", placeholder="默认 0"),
        _select(
            "redial_condition",
            "重拨条件（可多选）",
            [_option(label, value) for value, label in REDIAL_LABELS],
            multiple=True,
            placeholder="不选则不限",
        ),
        _select("cs_staff_group_id", "转人工坐席组", staff_options, placeholder="默认不转人工"),
        _select("sms_template_id", "短信模板", sms_options, placeholder="默认不发送短信"),
        (
            _select("customer_white_group_ids", "客户白名单组（可多选）", white_options, multiple=True)
            if white_options
            else _input("customer_white_group_ids", "客户白名单组 ID（逗号分隔）", placeholder="可不填")
        ),
        _select("enable_elastic_robot", "弹性 BOT", [_option("否", "false"), _option("是", "true")]),
        _submit_button("下一步（留空取默认值）", "task_create_step2", {"token": token}),
        _submit_button("全部使用默认值跳过", "task_create_skip", {"token": token}),
    ]

    elements.append(
        {
            "tag": "form",
            "name": "create_task_step2",
            "direction": "vertical",
            "vertical_spacing": "8px",
            "elements": form_elements,
        }
    )
    # 第二步正常提交时不提供取消；校验失败时给出退出入口
    if error:
        elements.append(_columns(_callback_button("取消并退出", "task_form_cancel", {"token": token})))
    return _base_card("创建外呼任务 · 第 2 步（选填）", elements)


def build_start_form(*, token: str, jobs: list[dict], error: str = "") -> dict:
    """启动任务：下拉选择 + 手填 ID 兜底。"""
    elements: list[dict] = []
    if error:
        elements.append(_error_line(error))

    job_options = [
        _option(f"{row.get('name')}（{row.get('status')}）", row.get("id")) for row in jobs or []
    ]
    form_elements: list[dict] = []
    if job_options:
        form_elements.append(
            _select("robot_call_job_id_select", "选择任务", job_options, placeholder="请选择要启动的任务")
        )
    form_elements.append(
        _input("robot_call_job_id", "或手填任务 ID", placeholder="选项里没有时可手填任务 ID")
    )
    form_elements.append(_submit_button("下一步", "task_start_step1", {"token": token}))

    elements.append(
        {
            "tag": "form",
            "name": "start_task",
            "direction": "vertical",
            "vertical_spacing": "8px",
            "elements": form_elements,
        }
    )
    elements.append(
        _columns(
            _callback_button("刷新任务列表", "task_form_refresh", {"kind": "start"}),
            _callback_button("取消", "task_form_cancel", {"token": token}),
        )
    )
    return _base_card("启动外呼任务", elements, template="turquoise")


def build_task_confirm(*, token: str, kind: str, fields: list[tuple[str, Any]]) -> dict:
    """二次确认卡：只读摘要 + 确认/取消。"""
    lines = ["确认后才会真正提交到一知平台：", ""]
    for label, value in fields:
        lines.append(f"- **{label}**：{value if value not in (None, '') else '-'}")
    title = "确认创建外呼任务" if kind == "create" else "确认启动外呼任务"
    action = "task_create_confirm" if kind == "create" else "task_start_confirm"
    return _base_card(
        title,
        [
            _markdown("\n".join(lines)),
            _columns(
                _callback_button("取消", "task_form_cancel", {"token": token}),
                _callback_button("确认执行", action, {"token": token}, type_="primary"),
            ),
        ],
        template="orange",
    )


def build_result(title: str, content: str, ok: bool = True) -> dict:
    return _base_card(
        title,
        [_markdown(content)],
        template="green" if ok else "red",
    )


def build_processing(text: str = "正在处理，请稍候…") -> dict:
    return _base_card("处理中", [_markdown(text)], template="grey")
