"""一知外呼任务：状态查询与运行概况。

接口事实（2026-11-17 实测）：
- 任务列表  GET  /apiEngine/robotCallJob/listForCollection
  分页数据在 `simplePageInfo` 下；statusSet 为空时连已归档任务一起返回，需要本地过滤。
- 任务详情  GET  /apiEngine/robotCallJob/getById
- 任务状态  GET  /apiEngine/robotCallJob/getStatus（返回单键对象数组）
- 任务统计  GET  /apiEngine/callStats/callJobFullStatsInfo
  一次拿到 taskProgress、intentLevel、intentLevelTagDetailList、dialResult 等；
  注意 `/apiEngine/intentMessage/jobStatusCount` 是短信发送状态计数，不是意向分布。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.clients import tanyibot
from app.core import cache

SYSTEM_TYPE = "CALL_OUT"
PAGE_SIZE = 50
MAX_PAGES = 4
LIST_PAGE_SIZE = 10

STATUS_LABELS = {
    "NOT_STARTED": "未开始",
    "RUNNABLE": "待运行",
    "IN_QUEUE": "排队中",
    "IN_PROCESS": "进行中",
    "USER_PAUSE": "用户暂停",
    "SYSTEM_SUSPENDED": "系统暂停",
    "MAINTAIN": "系统维护",
    "WAITING_FOR_REDIAL": "等待重拨",
    "EXPIRED": "已过期",
    "SYSTEM_HANG_UP": "系统挂起",
    "COMPLETED": "已完成",
    "TERMINATE": "已终止",
    "ARCHIVED": "已归档",
    "PAUSED": "已暂停",
    "STOPPED": "已停止",
}

ENDED_STATUSES = {"COMPLETED", "TERMINATE", "ARCHIVED", "EXPIRED"}
PAUSED_STATUSES = {"USER_PAUSE", "SYSTEM_SUSPENDED", "MAINTAIN", "WAITING_FOR_REDIAL"}
RUNNING_STATUSES = {"IN_PROCESS", "RUNNABLE", "IN_QUEUE"}

ORDER_TYPES = {
    "CREATE_TIME": "按创建时间排序",
    "LAST_EDIT_TIME": "按最近更新排序",
    "DRAG_SORT": "按拖拽排序",
}

DAY_LABELS = {
    "MONDAY": "周一",
    "TUESDAY": "周二",
    "WEDNESDAY": "周三",
    "THURSDAY": "周四",
    "FRIDAY": "周五",
    "SATURDAY": "周六",
    "SUNDAY": "周日",
}

# 筛选卡下拉值 -> (列表接口 statusSet, 展示名)
STATUS_FILTERS = {
    "ALL": ("", "全部（不含已归档）"),
    "NOT_STARTED": ("NOT_STARTED", "未开始"),
    "RUNNING": ("IN_QUEUE,IN_PROCESS,RUNNABLE", "进行中 / 排队"),
    "PAUSED": ("USER_PAUSE,SYSTEM_SUSPENDED,MAINTAIN,WAITING_FOR_REDIAL", "已暂停 / 系统暂停"),
    "COMPLETED": ("COMPLETED", "已完成"),
    "TERMINATE": ("TERMINATE", "已终止"),
}


def status_label(status: str) -> str:
    return STATUS_LABELS.get((status or "").upper(), status or "-")


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _summary(row: dict) -> dict:
    status = str(row.get("status") or "")
    return {
        "id": _int(row.get("robotCallJobId")),
        "name": row.get("name") or f"任务-{row.get('robotCallJobId')}",
        "status": status,
        "status_label": status_label(status),
        "create_time": row.get("createTime") or "",
        "dialog_flow_name": row.get("dialogFlowName") or "",
    }


def query_jobs(
    *,
    status: str = "ALL",
    order_type: str = "CREATE_TIME",
    days: int = 15,
    name: str = "",
    max_jobs: int = 100,
) -> list[dict]:
    """按筛选条件拉任务，已归档任务一律不出现在结果里。"""
    status_set = (STATUS_FILTERS.get(status) or ("", ""))[0] if status else ""
    order = order_type if order_type in ORDER_TYPES else "CREATE_TIME"
    params = {
        "enableTop": "false",
        "folderId": "",
        "name": (name or "").strip(),
        "statusSet": status_set,
        "orderType": order,
        "jobIds": "",
        "systemType": SYSTEM_TYPE,
    }
    if days > 0:
        # 实测该接口只接受 YYYY-MM-DD，带时分秒会返回「系统未知错误」
        end = datetime.now()
        begin = end - timedelta(days=days - 1)
        params["beginCreateDate"] = begin.strftime("%Y-%m-%d")
        params["endCreateDate"] = end.strftime("%Y-%m-%d")
    else:
        params["beginCreateDate"] = ""
        params["endCreateDate"] = ""

    jobs: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = tanyibot.get(
            "/apiEngine/robotCallJob/listForCollection",
            params={**params, "pageNum": page, "pageSize": PAGE_SIZE},
        ) or {}
        page_info = data.get("simplePageInfo") or {}
        rows = page_info.get("content") or []
        for row in rows:
            if not isinstance(row, dict) or not row.get("robotCallJobId"):
                continue
            if str(row.get("status") or "") == "ARCHIVED":
                continue
            jobs.append(_summary(row))
            if len(jobs) >= max_jobs:
                return jobs
        if page >= _int(page_info.get("pages") or 1) or not rows:
            break
    return jobs


def find_jobs_by_keyword(keyword: str) -> list[dict]:
    """语义入口：纯数字按 ID 精确查，其余按名称包含匹配。"""
    text = (keyword or "").strip()
    if not text:
        return []
    if text.isdigit():
        try:
            detail = tanyibot.get(
                "/apiEngine/robotCallJob/getById",
                params={"robotCallJobId": int(text), "systemType": SYSTEM_TYPE},
            ) or {}
        except tanyibot.AiccError as exc:
            if exc.code == 404:
                return []
            raise
        job = detail.get("robotCallJob") or {}
        if not job:
            return []
        return [_summary(job)]

    jobs = query_jobs(name=text, days=0, max_jobs=50, status="ALL")
    lowered = text.lower()
    return [job for job in jobs if lowered in str(job.get("name") or "").lower()]


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


def _snapshot(job_id: int, force: bool = False) -> dict:
    key = f"aicc:task:status:{job_id}"
    if force:
        cache.delete(key)

    def loader() -> dict:
        detail = tanyibot.get(
            "/apiEngine/robotCallJob/getById",
            params={"robotCallJobId": job_id, "systemType": SYSTEM_TYPE},
        ) or {}
        stats = tanyibot.get(
            "/apiEngine/callStats/callJobFullStatsInfo",
            params={"robotCallJobId": job_id, "systemType": SYSTEM_TYPE},
        ) or {}
        return {
            "detail": detail,
            "status": fetch_status(job_id),
            "stats": stats,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    data, _hit = cache.get_or_load(key, loader, cache.TASK_STATUS_TTL)
    return data or {}


def _parse_dt(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_date(raw: str) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _in_working_window(job: dict, now: datetime) -> bool:
    days = [str(item).upper() for item in (job.get("daysOfWeek") or [])]
    if days and now.strftime("%A").upper() not in days:
        return False
    start = str(job.get("dailyStartTime") or "00:00:00")
    end = str(job.get("dailyEndTime") or "23:59:59")
    current = now.strftime("%H:%M:%S")
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _intent_rows(stats: dict) -> list[dict]:
    counts = stats.get("intentLevel") or {}
    names = {
        _int(row.get("code")): row.get("name") or f"{row.get('code')} 级"
        for row in (stats.get("intentLevelTagDetailList") or [])
        if isinstance(row, dict)
    }
    total = sum(_int(value) for value in counts.values())
    rows: list[dict] = []
    for key, value in counts.items():
        count = _int(value)
        if count <= 0:
            continue
        code = _int(key)
        rows.append(
            {
                "name": names.get(code, f"{code} 级"),
                "count": count,
                "share": (count / total * 100) if total else 0.0,
            }
        )
    rows.sort(key=lambda item: item["count"], reverse=True)
    return rows


def _build_hints(job: dict, stats: dict, status: str, now: datetime) -> list[str]:
    hints: list[str] = []
    total = _int(stats.get("totalTask"))
    label = status_label(status)

    if total <= 0:
        hints.append("任务还没有客户，先在平台导入客户后才能外呼")
    if status in ENDED_STATUSES:
        hints.append(f"任务{label}，以下数据为最终结果")
    if status == "NOT_STARTED":
        next_start = _parse_dt(job.get("nextStartDatetime") or job.get("startTime"))
        if next_start and next_start < now:
            hints.append("已过计划启动时间仍未开始：确认客户是否导入、线路是否可用")
    if status in PAUSED_STATUSES:
        hints.append(f"任务处于「{label}」，需要在平台恢复后才会继续外呼")
    if status in RUNNING_STATUSES and not _in_working_window(job, now):
        hints.append(
            "当前不在拨打时段："
            f"{job.get('dailyStartTime') or '-'} - {job.get('dailyEndTime') or '-'}"
        )
    if status in RUNNING_STATUSES:
        last_call = _parse_date(stats.get("lastCallOutDate"))
        if last_call and (now.date() - last_call).days >= 2:
            hints.append("近两天没有新的外呼记录，检查任务是否被系统暂停")
    filtered_rate = _float(stats.get("filteredRate"))
    if filtered_rate >= 30:
        hints.append(f"过滤率偏高（{filtered_rate:.1f}%），检查客户名单、黑名单与去重规则")
    if _int(job.get("concurrencyQuota")) <= 0:
        hints.append("并发数未配置，任务无法实际外呼")
    return hints[:3]


def build_overview(job_id: int, force: bool = False) -> dict:
    """读取并整理成卡片直接可渲染的概况结构。"""
    snapshot = _snapshot(int(job_id), force=force)
    detail = snapshot.get("detail") or {}
    stats = snapshot.get("stats") or {}
    status_row = snapshot.get("status") or {}
    job = detail.get("robotCallJob") or {}
    if not job:
        raise tanyibot.AiccError(404, f"任务 {job_id} 不存在或无权访问")

    status = str(status_row.get("status") or job.get("status") or "")
    total = _int(stats.get("totalTask"))
    completed = _int(stats.get("taskTotalCompleted"))
    filtered = _int(stats.get("filteredTaskTotalCount"))
    percent = _float(stats.get("taskProgress"))
    if not percent and total:
        percent = completed / total * 100

    answered = _int(stats.get("answeredTask"))
    duration_total = _float(stats.get("chatDurationTotal"))
    avg_duration = duration_total / answered if answered else 0.0

    lines = [
        str(row.get("phoneNumber") or row.get("phoneName") or row.get("tenantPhoneNumberId"))
        for row in (detail.get("jobPhoneNumberList") or [])
        if isinstance(row, dict)
    ]
    raw_days = {str(value).upper() for value in (job.get("daysOfWeek") or [])}
    days = [label for key, label in DAY_LABELS.items() if key in raw_days]

    now = datetime.now()
    return {
        "job_id": _int(job.get("robotCallJobId") or job_id),
        "name": job.get("name") or f"任务-{job_id}",
        "status": status,
        "status_label": status_label(status),
        "ended": status in ENDED_STATUSES,
        "progress": {
            "total": total,
            "completed": completed,
            "filtered": filtered,
            "percent": percent,
        },
        "call": {
            "total": _int(stats.get("taskCallTotal")),
            "answered": answered,
            "rate": _float(stats.get("answeredRate")),
            "avg_duration": avg_duration,
            "hangup_rate": _float(stats.get("customerHangupPercent")),
            "ab_ratio": _float(stats.get("abRatio")),
            "last_call_date": stats.get("lastCallOutDate") or "-",
        },
        "intents": _intent_rows(stats),
        "config": {
            "dialog_flow": detail.get("dialogFlowName") or "-",
            "lines": "、".join(item for item in lines if item) or "-",
            "concurrency": _int(job.get("concurrencyQuota")),
            "robot_count": _int(job.get("robotCount")),
            "daily": f"{job.get('dailyStartTime') or '-'} - {job.get('dailyEndTime') or '-'}",
            "days": "、".join(days) or "-",
            "priority": _int(job.get("priority")),
            "created_by": detail.get("createdByUserName") or "-",
            "next_start": (
                None
                if status in ENDED_STATUSES
                else (status_row.get("nextStartDatetime") or job.get("nextStartDatetime"))
            ),
        },
        "hints": _build_hints(job, stats, status, now),
        "fetched_at": snapshot.get("fetched_at") or now.strftime("%Y-%m-%d %H:%M:%S"),
    }
