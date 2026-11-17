"""一知 AI 外呼任务：创建与启动（业务函数，供卡片流程调用）。

接口事实（2026-11-17 实测）：
- 查重：GET /apiEngine/robotCallJob/checkName?name=&systemType= → data 为 bool
  （true = 名称已被占用；原文标注 POST，实测 POST 返回 412）
- 创建：POST /apiEngine/robotCallJob/create
  除文档字段外，平台隐藏必填 robotCallJob.baseRobotCount / baseConcurrencyQuota，
  缺失时报 `412 AI坐席数不能为空`
- 启动：POST /apiEngine/robotCallJob/execute?robotCallJobId=&operation=START
  参数走 query；任务无客户时返回 `428 未导入客户`
"""

from __future__ import annotations

from datetime import datetime

from app.clients import tanyibot

SYSTEM_TYPE = "CALL_OUT"
PHONE_TYPES = ("LANDLINE", "MOBILE")
DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
MODES = ("MANUAL", "AUTO")
REDIAL_CONDITIONS = (
    "CALL_LOSS", "NO_ANSWER", "BUSY", "REFUSED", "POWER_OFF",
    "OUT_OF_SERVICE", "CAN_NOT_CONNECT", "FROM_PHONE_ERROR", "SYSTEM_ERROR",
)
_DAY_ALIASES = {
    "周一": "MONDAY", "星期一": "MONDAY", "礼拜一": "MONDAY",
    "周二": "TUESDAY", "星期二": "TUESDAY", "礼拜二": "TUESDAY",
    "周三": "WEDNESDAY", "星期三": "WEDNESDAY", "礼拜三": "WEDNESDAY",
    "周四": "THURSDAY", "星期四": "THURSDAY", "礼拜四": "THURSDAY",
    "周五": "FRIDAY", "星期五": "FRIDAY", "礼拜五": "FRIDAY",
    "周六": "SATURDAY", "星期六": "SATURDAY", "礼拜六": "SATURDAY",
    "周日": "SUNDAY", "周天": "SUNDAY", "星期日": "SUNDAY", "星期天": "SUNDAY", "礼拜日": "SUNDAY",
}
_STARTABLE = {"NOT_STARTED", "PAUSED", "USER_PAUSE", "STOPPED"}
_RUNNING = {"IN_PROGRESS"}
_FINISHED = {"COMPLETED", "ARCHIVED"}


def normalize_time(raw: str, label: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"{label}格式不正确（{text}），请用 HH:MM:SS，例如 09:00:00")


def normalize_days(raw: list[str] | str) -> list[str]:
    if isinstance(raw, str):
        raw = [part for part in raw.replace("，", ",").split(",") if part.strip()]
    if not raw:
        raise ValueError("拨打星期不能为空")
    picked: set[str] = set()
    unknown: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text:
            continue
        upper = text.upper()
        day = upper if upper in DAYS else _DAY_ALIASES.get(text)
        if day:
            picked.add(day)
        else:
            unknown.append(text)
    if unknown:
        raise ValueError(
            f"无法识别的星期：{'、'.join(unknown)}；请用 MONDAY…SUNDAY 或「周一」这类写法"
        )
    if not picked:
        raise ValueError("拨打星期不能为空")
    return [day for day in DAYS if day in picked]


def check_lines(ids: list[int], phone_type: str) -> tuple[list[dict], int]:
    """校验线路可用性，返回 (可用线路对象, 并发上限)。"""
    if not ids:
        raise ValueError("主叫线路不能为空，请先查询可用线路 ID")

    data = tanyibot.get("/apiEngine/tenantPhoneNumber/getActive") or {}
    lines = data.get(phone_type) or []
    by_id = {
        int(line["tenantPhoneNumberId"]): line
        for line in lines
        if line.get("tenantPhoneNumberId") is not None
    }

    problems: list[str] = []
    usable: list[dict] = []
    for pid in ids:
        line = by_id.get(int(pid))
        if not line:
            problems.append(f"{pid} 不在 {phone_type} 的可用线路中")
            continue
        if not line.get("supportCallOut"):
            problems.append(f"{pid} 不支持外呼")
            continue
        if line.get("stopStatus"):
            problems.append(f"{pid} 已停机")
            continue
        if line.get("enabledStatus") != 1:
            problems.append(f"{pid} 未启用")
            continue
        usable.append(line)

    if problems:
        raise ValueError("；".join(problems))

    limits = [
        int(line.get("concurrenceLimit") or 0)
        for line in usable
        if int(line.get("concurrenceLimit") or 0) > 0
    ]
    return usable, (min(limits) if limits else 0)


def line_type_of(ids: list[int]) -> str:
    """根据线路 ID 反查线路类型（LANDLINE / MOBILE）。"""
    data = tanyibot.get("/apiEngine/tenantPhoneNumber/getActive") or {}
    for phone_type, lines in data.items():
        for line in lines or []:
            if int(line.get("tenantPhoneNumberId") or 0) in ids:
                return phone_type
    raise ValueError(f"线路 {ids} 不在任何可用分组中")


def _extract_job_id(data) -> int | None:
    if isinstance(data, int):
        return data
    if isinstance(data, dict):
        for key in ("robotCallJobId", "id"):
            value = data.get(key)
            if isinstance(value, int):
                return value
        inner = data.get("robotCallJob")
        if isinstance(inner, dict) and isinstance(inner.get("robotCallJobId"), int):
            return inner["robotCallJobId"]
    return None


def _error_text(exc: Exception, action: str) -> str:
    if isinstance(exc, tanyibot.AiccError):
        if exc.code == 400:
            return f"{action}失败：{exc.message}（如持续出现，请检查 .env 中的账号密码）"
        if exc.code == 428:
            return f"{action}失败：{exc.message}（任务还没有导入客户，平台不允许启动）"
        if exc.code == 404:
            return f"{action}失败：{exc.message}（任务不存在或无权访问）"
        return f"{action}失败：{exc.message}（错误码 {exc.code}）"
    return f"{action}失败：{exc}"


def create_call_task(
    *,
    name: str,
    dialog_flow_id: int,
    phone_number_ids: list[int],
    concurrency_quota: int,
    robot_count: int,
    daily_start_time: str,
    daily_end_time: str,
    days_of_week: list[str] | str,
    mode: str = "MANUAL",
    priority: int = 100,
    on_working_days: bool = False,
    description: str = "",
    folder_id: int | None = None,
    redial_times: int = 0,
    redial_interval: int = 0,
    redial_condition: list[str] | None = None,
    cs_staff_group_id: int = 0,
    transfer_phone_choose_type: str = "POLLING",
    cs_distribution_method: str = "FREE",
    sms_template_id: int = 0,
    sms_push_type: str = "EVERY_PUSH",
    enable_elastic_robot: bool = False,
    customer_white_group_ids: list[int] | None = None,
) -> str:
    """创建外呼任务，返回给用户看的纯文本结果。"""
    try:
        name = (name or "").strip()
        if not name:
            raise ValueError("任务名称不能为空")

        if not isinstance(dialog_flow_id, int) or dialog_flow_id <= 0:
            raise ValueError("话术（BOT）必须是正整数 ID")

        concurrency = int(concurrency_quota)
        if concurrency < 1:
            raise ValueError("并发数必须大于等于 1")

        robots = int(robot_count)
        if robots < 1:
            raise ValueError("BOT 数量必须大于等于 1")

        start_time = normalize_time(daily_start_time, "每日开始时间")
        end_time = normalize_time(daily_end_time, "每日结束时间")
        if start_time >= end_time:
            raise ValueError("每日开始时间必须早于结束时间")

        days = normalize_days(days_of_week)

        mode = (mode or "MANUAL").strip().upper()
        if mode not in MODES:
            raise ValueError("启动方式只能是 MANUAL（手动）或 AUTO（自动）")

        redial_times = int(redial_times)
        redial_interval = int(redial_interval)
        if redial_times < 0 or redial_interval < 0:
            raise ValueError("重拨次数与重拨间隔不能为负数")

        conditions = [str(item).strip().upper() for item in (redial_condition or []) if str(item).strip()]
        invalid = [item for item in conditions if item not in REDIAL_CONDITIONS]
        if invalid:
            raise ValueError(f"不支持的重拨条件：{'、'.join(invalid)}")

        ids = [int(pid) for pid in phone_number_ids]
        phone_type = line_type_of(ids)
        usable, limit = check_lines(ids, phone_type)
        if limit and concurrency > limit:
            raise ValueError(
                f"并发数 {concurrency} 超过线路并发上限 {limit}"
                f"（线路 {', '.join(str(line['tenantPhoneNumberId']) for line in usable)}）"
            )

        if tanyibot.get(
            "/apiEngine/robotCallJob/checkName",
            params={"name": name, "systemType": SYSTEM_TYPE},
        ):
            raise ValueError(f"任务名「{name}」已存在，请换一个名称")

        body = {
            "concurrencyQuota": concurrency,
            "jobPhoneNumberIdList": ids,
            "robotCallJob": {
                "name": name,
                "dialogFlowId": dialog_flow_id,
                "phoneType": phone_type,
                "mode": mode,
                "robotCount": robots,
                "baseRobotCount": robots,
                "baseConcurrencyQuota": concurrency,
                "dailyStartTime": start_time,
                "dailyEndTime": end_time,
                "daysOfWeek": days,
                "onWorkingDays": bool(on_working_days),
                "csStaffGroupId": int(cs_staff_group_id or 0),
                "transferPhoneChooseType": transfer_phone_choose_type or "POLLING",
                "csDistributionMethod": cs_distribution_method or "FREE",
                "concurrencyQuota": concurrency,
                "priority": int(priority),
                "robotCallJobType": "COMMON",
                "description": (description or "").strip(),
                "redialTimes": redial_times,
                "redialInterval": redial_interval,
                "redialCondition": conditions,
                "enableElasticRobot": bool(enable_elastic_robot),
                "smsTemplateId": int(sms_template_id or 0),
                "smsPushType": sms_push_type or "EVERY_PUSH",
                "customerWhiteGroupIds": [int(item) for item in (customer_white_group_ids or [])],
            },
        }
        if folder_id:
            body["robotCallJob"]["folderId"] = int(folder_id)

        job_id = _extract_job_id(tanyibot.post("/apiEngine/robotCallJob/create", body))
    except ValueError as exc:
        return f"创建任务失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc, "创建任务")

    lines_text = "、".join(
        f"{line['tenantPhoneNumberId']}（{line.get('phoneNumber') or '-'}）" for line in usable
    )
    return "\n".join(
        [
            "✅ 外呼任务已创建",
            f"- 任务 ID：{job_id if job_id is not None else '未返回，请到平台确认'}",
            f"- 任务名称：{name}",
            f"- 话术 ID：{dialog_flow_id}",
            f"- 主叫线路：{lines_text}",
            f"- 线路类型：{phone_type}",
            f"- 并发 / BOT：{concurrency} / {robots}",
            f"- 拨打时段：{start_time} - {end_time}",
            f"- 拨打星期：{'、'.join(days)}",
            f"- 启动方式：{mode}",
            "- 当前状态：NOT_STARTED（尚未导入客户，需导入客户后才能实际外呼）",
        ]
    )


def start_call_task(robot_call_job_id: int) -> str:
    """启动任务，返回给用户看的纯文本结果。"""
    try:
        job_id = int(robot_call_job_id)
        if job_id <= 0:
            raise ValueError("任务 ID 必须是正整数")

        detail = tanyibot.get(
            "/apiEngine/robotCallJob/getById",
            params={"robotCallJobId": job_id, "systemType": SYSTEM_TYPE},
        ) or {}
        job = detail.get("robotCallJob") or {}
        if not job:
            return f"启动任务失败：任务 {job_id} 不存在或无权访问"

        status = str(job.get("status") or "")
        name = job.get("name") or "-"
        if status in _RUNNING:
            return f"任务「{name}」（{job_id}）已经在运行中，无需重复启动"
        if status in _FINISHED:
            return f"任务「{name}」（{job_id}）状态为 {status}，不能再次启动"
        if status and status not in _STARTABLE:
            return f"任务「{name}」（{job_id}）当前状态为 {status}，暂时不能启动"

        tanyibot.post(
            "/apiEngine/robotCallJob/execute",
            params={"robotCallJobId": job_id, "operation": "START"},
        )
        latest = fetch_status(job_id)
    except ValueError as exc:
        return f"启动任务失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc, "启动任务")

    lines = [
        "✅ 启动指令已提交",
        f"- 任务 ID：{job_id}",
        f"- 任务名称：{name}",
        f"- 话术：{detail.get('dialogFlowName') or '-'}",
        f"- 启动前状态：{status or '-'}",
        f"- 最新状态：{latest.get('status') or '暂未返回'}",
    ]
    next_start = latest.get("nextStartDatetime") or latest.get("nextStartTime")
    if next_start:
        lines.append(f"- 下次启动时间：{next_start}")
    if (latest.get("status") or status) == "NOT_STARTED":
        lines.append("- 提示：状态仍为 NOT_STARTED，可能因未导入客户或当前不在拨打时段")
    return "\n".join(lines)


def fetch_status(job_id: int) -> dict:
    """getStatus 返回单键对象数组，需要遍历取值。"""
    rows = tanyibot.get(
        "/apiEngine/robotCallJob/getStatus",
        params={"robotCallJobIds": str(job_id), "systemType": SYSTEM_TYPE},
    ) or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(str(job_id))
        if isinstance(value, dict):
            return value
    return {}


def job_brief(job_id: int) -> dict:
    """读取任务摘要，供确认卡展示。"""
    detail = tanyibot.get(
        "/apiEngine/robotCallJob/getById",
        params={"robotCallJobId": int(job_id), "systemType": SYSTEM_TYPE},
    ) or {}
    job = detail.get("robotCallJob") or {}
    return {
        "id": int(job.get("robotCallJobId") or job_id),
        "name": job.get("name") or "-",
        "status": job.get("status") or "-",
        "dialog_flow_name": detail.get("dialogFlowName") or "-",
        "phone_type": job.get("phoneType") or "-",
        "concurrency": job.get("concurrencyQuota"),
        "robot_count": job.get("robotCount"),
        "daily": f"{job.get('dailyStartTime') or '-'} - {job.get('dailyEndTime') or '-'}",
    }
