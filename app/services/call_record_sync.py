"""通话记录拉取入库：任务发现、列表 + 详情归一化、幂等写入。

与回调入库共用 call_record 表与字段；拉取来源标记为 PULL_INTERNAL。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from app.callback.parsers import _build_transcript
from app.clients import aicc_call_record, tanyibot
from app.core.aicc_enums import ENDED_TASK_STATUSES, normalize_call_status
from app.repositories import call_record_repo, sync_log_repo
from app.services import ingest
from app.tools import aicc_task_status

log = logging.getLogger("call_record_sync")

DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")
PULL_SOURCE = "PULL_INTERNAL"


def discover_jobs(days: int = 90, explicit_jobs: list[int] | None = None) -> list[int]:
    """显式任务 + AICC 近 N 天任务，去重保序。"""
    jobs: list[int] = []
    for job_id in explicit_jobs or []:
        try:
            jobs.append(int(job_id))
        except (TypeError, ValueError):
            continue

    try:
        recent = aicc_task_status.query_jobs(days=max(1, int(days or 90)), max_jobs=200)
        for row in recent:
            if str(row.get("status") or "").upper() in ENDED_TASK_STATUSES:
                continue
            job_id = int(row.get("id") or 0)
            if job_id and job_id not in jobs:
                jobs.append(job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("自动发现任务失败，仅使用显式任务：%s", exc)

    return jobs


def sync_jobs(
    jobs: list[int],
    *,
    days: int = 7,
    start: str | None = None,
    end: str | None = None,
    sleep: float = 2.5,
    limit: int = 0,
    dry_run: bool = False,
    with_profile: bool = True,
) -> list[dict]:
    """逐任务同步；每个任务独立游标与 sync_log，失败不阻断其他任务。"""
    end_dt = _parse_dt(end) or datetime.now()
    summaries: list[dict] = []

    for job_id in jobs:
        if start:
            job_start = _parse_dt(start) or (end_dt - timedelta(days=days))
        else:
            cursor = sync_log_repo.get_cursor(f"call_record:{job_id}")
            job_start = (
                cursor - timedelta(days=1)
                if cursor
                else end_dt - timedelta(days=max(1, int(days or 7)))
            )

        run_id: int | None = None
        if not dry_run:
            run_id = sync_log_repo.start_run(f"call_record:{job_id}")

        try:
            summary = sync_job(
                job_id,
                job_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                sleep=sleep,
                limit=limit,
                dry_run=dry_run,
                with_profile=with_profile,
            )
            summary["status"] = "SUCCESS"
            if run_id is not None:
                sync_log_repo.finish_run(
                    run_id,
                    status="SUCCESS",
                    fetched=summary["fetched"],
                    saved=summary["saved"],
                    cursor_time=summary["cursor_time"] or end_dt,
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("任务 %s 同步失败", job_id)
            summary = {
                "job_id": job_id,
                "status": "FAILED",
                "error": str(exc),
                "fetched": 0,
                "saved": 0,
                "records": [],
                "cursor_time": None,
            }
            if run_id is not None:
                sync_log_repo.finish_run(
                    run_id,
                    status="FAILED",
                    error_msg=str(exc),
                )
        summaries.append(summary)

    return summaries


def sync_job(
    job_id: int,
    start: str,
    end: str,
    *,
    sleep: float = 2.5,
    limit: int = 0,
    dry_run: bool = False,
    with_profile: bool = True,
) -> dict:
    """同步单个任务，返回统计与归一化后的记录列表。"""
    rows = aicc_call_record.list_records(int(job_id), start, end)
    records: list[dict] = []
    saved = 0
    notified = 0
    detail_calls = 0

    for row in rows:
        if limit and len(records) >= limit:
            break

        detail: dict = {}
        result_status = str(row.get("resultStatus") or "")
        rounds = _to_int(row.get("chatRound"))
        existing = call_record_repo.get_call_record(str(row.get("callRecordId") or ""))
        has_transcript = bool(existing and existing.get("asr_transcript"))
        if result_status == "ANSWERED" and rounds > 0 and not has_transcript:
            if detail_calls:
                time.sleep(max(0.0, float(sleep or 0)))
            try:
                detail = aicc_call_record.fetch_detail(row.get("callRecordId"))
                detail_calls += 1
            except tanyibot.AiccError as exc:
                log.warning(
                    "拉取通话详情失败 job=%s record=%s：%s",
                    job_id,
                    row.get("callRecordId"),
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "拉取通话详情异常 job=%s record=%s：%s",
                    job_id,
                    row.get("callRecordId"),
                    exc,
                )

        record = normalize_row(row, detail)
        if not record.get("record_id") or not record.get("customer_phone"):
            log.warning("跳过缺少 record_id / 号码的记录：%s", row.get("callRecordId"))
            continue
        records.append(record)
        if dry_run:
            continue

        result = ingest.persist_record(record, with_profile=with_profile)
        if result.get("saved"):
            saved += 1
        if result.get("notice_id"):
            notified += 1

    cursor_time = max(
        (row["call_time"] for row in records if isinstance(row.get("call_time"), datetime)),
        default=None,
    )
    log.info(
        "任务 %s 拉取完成：列表 %s 条，详情 %s 次，入库 %s 条，派单 %s 条",
        job_id,
        len(rows),
        detail_calls,
        saved,
        notified,
    )
    return {
        "job_id": int(job_id),
        "fetched": len(rows),
        "saved": saved,
        "notified": notified,
        "detail_calls": detail_calls,
        "records": records,
        "cursor_time": cursor_time,
    }


def normalize_row(list_row: dict, detail: dict) -> dict:
    """列表字段打底，详情字段覆盖。"""
    detail = detail or {}
    phone = _text(list_row.get("calledPhoneNumber") or detail.get("calledPhoneNumber"))
    call_time = _parse_dt(list_row.get("startTime") or detail.get("startTime"))
    if call_time is None:
        call_time = datetime.now().replace(microsecond=0)
        log.warning("通话 %s 缺少开始时间，使用当前时间兜底", list_row.get("callRecordId"))

    result_status = str(list_row.get("resultStatus") or detail.get("resultStatus") or "")
    properties = _as_dict(detail.get("properties"))
    provider_job_id = _text(list_row.get("robotCallJobId") or detail.get("robotCallJobId"))

    credit_code = _pick(properties, ("credit_code", "creditCode", "统一社会信用代码", "信用代码"))
    if not credit_code:
        credit_code = call_record_repo.find_credit_code_by_phone(phone)
    local_job_no = _pick(properties, ("local_job_no", "localJobNo", "任务编号", "我方任务编号"))
    if not local_job_no:
        local_job_no = call_record_repo.get_local_job_no_by_provider_job(provider_job_id)

    transcript, first_message, last_message = _build_transcript(detail.get("callDetailList"))
    dynamic_properties = _as_dict(detail.get("dynamicVariables")) or _as_dict(detail.get("extraInfo"))

    tag_id = detail.get("intentLevelTagId")
    if tag_id is None:
        tag_id = list_row.get("intentLevelTagId")
    intent_code = detail.get("intentLevelCode")
    if intent_code is None:
        intent_code = list_row.get("intentLevel")

    return {
        "record_id": str(list_row.get("callRecordId") or detail.get("callRecordId")),
        "credit_code": credit_code,
        "customer_name": _text(
            list_row.get("customerPersonName")
            or detail.get("customerPersonName")
            or (detail.get("accountVO") or {}).get("name")
        ),
        "customer_phone": phone,
        "local_job_no": local_job_no,
        "provider_job_id": provider_job_id,
        "call_time": call_time,
        "duration": _to_int(list_row.get("chatDuration") or detail.get("chatDuration")),
        "call_status": normalize_call_status(result_status),
        "status_raw": result_status,
        "supplier_level": _text(
            detail.get("intentLevelName") or list_row.get("aiIntentLevelName")
        ),
        "intent_level_tag_id": _to_int_or_none(tag_id),
        "intent_level_code": _to_int_or_none(intent_code),
        "hangup_by": _text(detail.get("hangupBy")),
        "chat_round": _to_int_or_none(detail.get("chatRound") or list_row.get("chatRound")),
        "ring_duration": _to_int_or_none(detail.get("ringDuration")),
        "analysis_basis": _text(
            detail.get("analysisBasis") or list_row.get("analysisBasis")
        ),
        "customer_concern": _join_list(
            detail.get("customerConcern") or list_row.get("customerConcern")
        ),
        "record_url": _text(detail.get("fullAudioUrl") or list_row.get("fullAudioUrl")),
        "record_expire_at": _record_expire_at(detail.get("expires"), call_time),
        "asr_transcript": transcript,
        "first_message": first_message,
        "last_message": last_message,
        "properties": properties or None,
        "dynamic_properties": dynamic_properties or None,
        "callback_data_type": PULL_SOURCE,
        "raw_json": {"source": PULL_SOURCE, "list": list_row, "detail": detail},
    }


def _record_expire_at(expires: Any, call_time: datetime) -> date:
    """优先用详情 expires（相对今天的天数，负数表示已过期），缺失按 90 天。"""
    value = _to_int_or_none(expires)
    if value is not None:
        return date.today() + timedelta(days=value)
    return (call_time + timedelta(days=90)).date()


def _parse_dt(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _join_list(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ",".join(parts) or None
    return _text(value)


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _pick(values: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None
