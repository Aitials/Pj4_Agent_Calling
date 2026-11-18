"""回调报文解析与新旧版归一化。

一知目前有两套通话记录回调：
- 旧版 dataType = ROBOT_CALL_RECORD
- 新版 dataType = RECONFIGURATION_ROBOT_CALL_RECORD

两者字段名不同，这里统一成 CallRecordEvent，后续入库只认这一种结构。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.aicc_enums import normalize_call_status

log = logging.getLogger("aicc_callback.parser")

NEW_DATA_TYPE = "RECONFIGURATION_ROBOT_CALL_RECORD"
OLD_DATA_TYPE = "ROBOT_CALL_RECORD"

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
)


@dataclass
class CallRecordEvent:
    record_id: str
    data_type: str
    customer_phone: str
    provider_job_id: str | None = None
    customer_name: str | None = None
    call_time: datetime | None = None
    duration: int = 0
    call_status: str = "UNKNOWN"
    status_raw: str = ""
    intent_level_tag_id: int | None = None
    intent_level_code: int | None = None
    supplier_level: str | None = None
    analysis_basis: str | None = None
    customer_concern: str | None = None
    hangup_by: str | None = None
    chat_round: int | None = None
    ring_duration: int | None = None
    record_url: str | None = None
    properties: dict = field(default_factory=dict)
    dynamic_properties: dict = field(default_factory=dict)
    transcript: str | None = None
    first_message: str | None = None
    last_message: str | None = None
    raw: dict = field(default_factory=dict)


def parse_payload(plaintext: str) -> CallRecordEvent | None:
    """把解密后的 JSON 明文转成统一事件；不支持的类型或缺少关键字段时返回 None。"""
    try:
        payload = json.loads(plaintext)
    except json.JSONDecodeError as exc:
        raise ValueError("回调明文不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("回调明文不是 JSON 对象")

    data_type = str(payload.get("dataType") or "").strip()
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("回调缺少 data 对象")

    if data_type == NEW_DATA_TYPE:
        event = _normalize_new(data)
    elif data_type == OLD_DATA_TYPE:
        event = _normalize_old(data)
    else:
        log.warning("忽略未订阅的回调类型：%s", data_type or "(空)")
        return None

    if event is None:
        return None
    event.raw = payload
    return event


# --------------------------------------------------------------------- 新版


def _normalize_new(data: dict) -> CallRecordEvent | None:
    record_id = _text(data.get("callOutRecordId"))
    phone = _text(data.get("calledPhoneNumber"))
    if not record_id or not phone:
        log.warning("新版通话记录回调缺少 callOutRecordId / calledPhoneNumber，已忽略")
        return None

    transcript, first_message, last_message = _build_transcript(data.get("callDetailList"))
    intent_name = _text(data.get("intentLevelName"))
    result_status = _text(data.get("resultStatus")) or ""

    return CallRecordEvent(
        record_id=record_id,
        data_type=NEW_DATA_TYPE,
        customer_phone=phone,
        provider_job_id=_text(data.get("callOutJobId")),
        customer_name=_text(data.get("accountName")),
        call_time=_parse_datetime(data.get("startTime")),
        duration=_to_int(data.get("chatDuration")),
        call_status=normalize_call_status(result_status),
        status_raw=result_status,
        intent_level_tag_id=_to_int_or_none(data.get("intentLevelTagId")),
        intent_level_code=_to_int_or_none(data.get("intentLevel")),
        supplier_level=intent_name,
        analysis_basis=_text(data.get("analysisBasis")),
        customer_concern=_join_list(data.get("customerConcern")),
        hangup_by=_text(data.get("hangupBy")),
        chat_round=_to_int_or_none(data.get("chatRound")),
        ring_duration=_to_int_or_none(data.get("ringDuration")),
        record_url=_text(data.get("fullAudioUrl")),
        properties=_as_dict(data.get("properties")),
        dynamic_properties=_as_dict(data.get("dynamicVariables")),
        transcript=transcript,
        first_message=first_message,
        last_message=last_message,
    )


# --------------------------------------------------------------------- 旧版


def _normalize_old(data: dict) -> CallRecordEvent | None:
    record_id = _text(data.get("callRecordId"))
    phone = _text(data.get("calledPhoneNumber"))
    if not record_id or not phone:
        log.warning("旧版通话记录回调缺少 callRecordId / calledPhoneNumber，已忽略")
        return None

    transcript, first_message, last_message = _build_transcript(data.get("callDetailList"))
    result_status = _text(data.get("resultStatus")) or ""

    return CallRecordEvent(
        record_id=record_id,
        data_type=OLD_DATA_TYPE,
        customer_phone=phone,
        provider_job_id=_text(data.get("robotCallJobId")),
        customer_name=_text(data.get("customerPersonName")),
        call_time=_parse_datetime(data.get("startTime")),
        duration=_to_int(data.get("chatDuration")),
        call_status=normalize_call_status(result_status),
        status_raw=result_status,
        intent_level_tag_id=_to_int_or_none(data.get("intentLevelTagId")),
        intent_level_code=_to_int_or_none(data.get("intentLevelCode")),
        supplier_level=_text(data.get("intentLevelDetailName")),
        analysis_basis=_text(data.get("analysisBasis")),
        customer_concern=_join_list(data.get("customerConcern")),
        hangup_by=_text(data.get("hangupBy")),
        chat_round=_to_int_or_none(data.get("chatRound")),
        ring_duration=_to_int_or_none(data.get("ringDuration")),
        record_url=_text(data.get("fullAudioUrl")),
        properties=_as_dict(data.get("properties")),
        dynamic_properties=_as_dict(data.get("dynamicProperties")),
        transcript=transcript,
        first_message=first_message,
        last_message=last_message,
    )


# ------------------------------------------------------------------ 工具函数


def _build_transcript(items: Any) -> tuple[str | None, str | None, str | None]:
    """把 callDetailList 拼成 [机器人]/[客户] 文本，并取首句机器人与末句客户。"""
    if not isinstance(items, list):
        return None, None, None

    lines: list[str] = []
    first_message: str | None = None
    last_message: str | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _text(item.get("text"))
        if not text:
            continue
        role = _role_name(item.get("type"))
        if role == "机器人" and first_message is None:
            first_message = text
        if role == "客户":
            last_message = text
        lines.append(f"[{role}] {text}")

    return ("\n".join(lines) or None), first_message, last_message


def _role_name(value: Any) -> str:
    if isinstance(value, str):
        upper = value.strip().upper()
        if upper in ("ROBOT", "2"):
            return "机器人"
        if upper in ("PERSON", "CUSTOMER", "USER", "1"):
            return "客户"
        return value.strip() or "未知"
    if value == 2:
        return "机器人"
    if value == 1:
        return "客户"
    return "未知"


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    log.warning("无法解析通话时间：%s", text)
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
