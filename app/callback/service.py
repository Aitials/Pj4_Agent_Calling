"""回调事件处理：补企业信息、计算录音过期日、幂等入库。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.callback.parsers import CallRecordEvent, parse_payload
from app.repositories import call_record_repo

log = logging.getLogger("aicc_callback.service")

RECORD_TTL_DAYS = 90

_CREDIT_KEYS = ("credit_code", "creditCode", "统一社会信用代码", "信用代码")
_JOB_KEYS = ("local_job_no", "localJobNo", "任务编号", "我方任务编号")


def handle_plaintext(plaintext: str) -> dict | None:
    """解密后的 JSON 明文 → 解析 → 入库；忽略不支持的类型。"""
    event = parse_payload(plaintext)
    if event is None:
        return None
    return handle_event(event)


def handle_event(event: CallRecordEvent) -> dict:
    """单个通话记录事件入库，返回便于日志与自检的摘要。"""
    credit_code = _pick(event.properties, _CREDIT_KEYS)
    if not credit_code:
        credit_code = call_record_repo.find_credit_code_by_phone(event.customer_phone)
    local_job_no = _pick(event.properties, _JOB_KEYS)

    call_time = event.call_time
    if call_time is None:
        call_time = datetime.now().replace(microsecond=0)
        log.warning("回调缺少 startTime，record_id=%s 使用当前时间兜底", event.record_id)

    row = {
        "record_id": event.record_id,
        "credit_code": credit_code,
        "customer_name": event.customer_name,
        "customer_phone": event.customer_phone,
        "local_job_no": local_job_no,
        "provider_job_id": event.provider_job_id,
        "call_time": call_time,
        "duration": event.duration,
        "call_status": event.call_status,
        "status_raw": event.status_raw,
        "supplier_level": event.supplier_level,
        "intent_level_tag_id": event.intent_level_tag_id,
        "intent_level_code": event.intent_level_code,
        "hangup_by": event.hangup_by,
        "chat_round": event.chat_round,
        "ring_duration": event.ring_duration,
        "analysis_basis": event.analysis_basis,
        "customer_concern": event.customer_concern,
        "record_url": event.record_url,
        "record_expire_at": (call_time + timedelta(days=RECORD_TTL_DAYS)).date(),
        "asr_transcript": event.transcript,
        "first_message": event.first_message,
        "last_message": event.last_message,
        "properties": event.properties or None,
        "dynamic_properties": event.dynamic_properties or None,
        "callback_data_type": event.data_type,
        "raw_json": event.raw,
    }
    call_record_repo.upsert_call_record(row)

    summary = {
        "record_id": event.record_id,
        "credit_code": credit_code,
        "phone": _mask_phone(event.customer_phone),
        "status": event.call_status,
        "duration": event.duration,
        "intent_level_code": event.intent_level_code,
        "transcript_lines": len(event.transcript.splitlines()) if event.transcript else 0,
    }
    log.info(
        "通话回调入库 record_id=%s job=%s phone=%s status=%s duration=%ss intent=%s/%s credit=%s lines=%s",
        event.record_id,
        event.provider_job_id or "-",
        summary["phone"],
        event.call_status,
        event.duration,
        event.intent_level_tag_id,
        event.intent_level_code,
        credit_code or "-",
        summary["transcript_lines"],
    )
    return summary


def _pick(values: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _mask_phone(phone: str) -> str:
    text = str(phone or "")
    if len(text) <= 7:
        return text
    return f"{text[:3]}****{text[-4:]}"
