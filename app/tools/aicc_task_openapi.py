"""一知 openapi：暂停任务的候选列表、任务详情与执行。

接口事实（2026-11-27 探针实测，账号 838 条任务全部在旧版体系）：
- 列表  GET  /apiOpen/v1/job/getJobs?status=&pageNum=&pageSize=  → data.content
- 详情  GET  /apiOpen/v1/job/getJobDetail?robotCallJobId=          → data.robotCallJob
- 暂停  POST /apiOpen/v1/job/pause  body {"robotCallJobId": <Long>}
  无效 ID 返回 code 404「查询的资源不存在」；
  状态不允许返回 code 412「该任务状态(xxx)不可暂停」。
"""

from __future__ import annotations

import logging

from app.clients import tanyibot_openapi
from app.core import cache
from app.core.aicc_enums import TASK_STATUS_LABELS

log = logging.getLogger("aicc_task_openapi")

# 暂停卡片只展示运行中三类，避免把已系统暂停/已结束的任务混进来
PAUSABLE_STATUSES = ("IN_PROCESS", "IN_QUEUE", "RUNNABLE")
PAUSABLE_LABELS = {status: TASK_STATUS_LABELS.get(status, status) for status in PAUSABLE_STATUSES}
# 已经是暂停态时直接提示，不再调平台
PAUSED_STATUSES = {"USER_PAUSE", "PAUSED"}

LIST_TTL = 60
PAGE_SIZE = 100
MAX_PAGES = 3


def status_label(status: str) -> str:
    return TASK_STATUS_LABELS.get((status or "").upper(), status or "-")


def _job_row(row: dict) -> dict | None:
    job_id = row.get("robotCallJobId")
    if not job_id:
        return None
    return {
        "id": int(job_id),
        "name": row.get("name") or f"任务-{job_id}",
        "status": row.get("status") or "",
    }


def pausable_jobs() -> list[dict]:
    """按运行中三类分别拉取任务，合并去重后缓存 60 秒；失败降级为空列表。"""
    def loader() -> list[dict]:
        jobs: dict[int, dict] = {}
        for status in PAUSABLE_STATUSES:
            for page in range(1, MAX_PAGES + 1):
                data = tanyibot_openapi.get(
                    "/apiOpen/v1/job/getJobs",
                    params={
                        "status": status,
                        "pageNum": page,
                        "pageSize": PAGE_SIZE,
                    },
                ) or {}
                rows = data.get("content") or []
                for row in rows:
                    job = _job_row(row)
                    if job and job["status"] in PAUSABLE_STATUSES:
                        jobs[job["id"]] = job
                if page >= int(data.get("pages") or 1) or not rows:
                    break
        ordered = sorted(jobs.values(), key=lambda item: -item["id"])
        return ordered

    try:
        data, _hit = cache.get_or_load("aicc:openapi:pausable_jobs", loader, LIST_TTL)
        return data or []
    except Exception as exc:  # noqa: BLE001 外部接口异常时退化为手填 ID，不阻断发卡
        log.warning("拉取可暂停任务失败：%s", exc)
        return []


def job_brief(job_id: int) -> dict:
    """读取任务摘要，供确认卡展示。"""
    data = tanyibot_openapi.get(
        "/apiOpen/v1/job/getJobDetail",
        params={"robotCallJobId": int(job_id)},
    ) or {}
    job = data.get("robotCallJob") or {}
    if not job:
        raise ValueError(f"任务 {job_id} 不存在或无权访问")
    return {
        "id": int(job.get("robotCallJobId") or job_id),
        "name": job.get("name") or "-",
        "status": job.get("status") or "",
        "status_label": status_label(job.get("status") or ""),
        "dialog_flow_name": job.get("dialogFlowName") or "-",
        "phone_type": job.get("phoneType") or data.get("phoneType") or "-",
        "concurrency": job.get("concurrencyQuota") or job.get("baseConcurrencyQuota"),
        "robot_count": job.get("robotCount") or job.get("baseRobotCount"),
        "daily": f"{job.get('dailyStartTime') or '-'} - {job.get('dailyEndTime') or '-'}",
        "next_start": job.get("nextStartDatetime") or "",
    }


def _error_text(exc: Exception, action: str) -> str:
    if isinstance(exc, tanyibot_openapi.OpenApiError):
        code = exc.code
        if code == 401:
            return (
                f"{action}失败：签名校验未通过，请检查 .env 中的 "
                "AICC_APP_KEY / AICC_APP_SECRET / AICC_TENANT_SIGN"
            )
        if code == 403:
            return f"{action}失败：当前账号没有该接口权限"
        if code == 404:
            return f"{action}失败：任务不存在或已被删除"
        if code == 412:
            hint = "（可先启动任务再试）" if "不可暂停" in exc.message else ""
            return f"{action}失败：{exc.message}{hint}"
        if code is None:
            return f"{action}失败：{exc.message}"
        return f"{action}失败：{exc.message}（错误码 {code}）"
    return f"{action}失败：{exc}"


def pause_task(robot_call_job_id: int) -> str:
    """暂停任务，返回给用户看的纯文本结果。"""
    try:
        try:
            job_id = int(robot_call_job_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("任务 ID 必须是数字") from exc
        if job_id <= 0:
            raise ValueError("任务 ID 必须是正整数")
        if not tanyibot_openapi.credentials_ready():
            return _error_text(
                tanyibot_openapi.OpenApiError(
                    None,
                    "未配置一知开放平台凭证（AICC_APP_KEY / AICC_APP_SECRET / AICC_TENANT_SIGN）",
                ),
                "暂停任务",
            )

        brief = job_brief(job_id)
        status = str(brief.get("status") or "")
        name = brief.get("name") or "-"
        if status in PAUSED_STATUSES:
            return f"✅ 任务「{name}」（{job_id}）已经是暂停状态，无需重复操作"
        if status not in PAUSABLE_STATUSES:
            return (
                f"❌ 任务「{name}」（{job_id}）当前状态为 {status_label(status)}，"
                "不在可暂停范围（进行中 / 排队中 / 可运行）"
            )

        tanyibot_openapi.post(
            "/apiOpen/v1/job/pause",
            json_body={"robotCallJobId": job_id},
        )
        latest = job_brief(job_id)
    except ValueError as exc:
        return f"暂停任务失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return _error_text(exc, "暂停任务")

    latest_status = str(latest.get("status") or "")
    lines = [
        "✅ 暂停指令已提交",
        f"- 任务 ID：{job_id}",
        f"- 任务名称：{name}",
        f"- 暂停前状态：{status_label(status)}",
        f"- 最新状态：{status_label(latest_status)}",
    ]
    if latest_status in PAUSED_STATUSES:
        lines.append("- 说明：任务已进入用户暂停，可随时通过「启动任务」恢复")
    else:
        lines.append("- 说明：暂停为软暂停，当前正在跑的通话结束后才会进入暂停状态")
    return "\n".join(lines)
