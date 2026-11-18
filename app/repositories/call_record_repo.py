"""call_record 数据访问层：回调幂等写入与查询。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("aicc_callback.repo")

_COLUMNS = (
    "record_id",
    "credit_code",
    "customer_name",
    "customer_phone",
    "local_job_no",
    "provider_job_id",
    "call_time",
    "duration",
    "call_status",
    "status_raw",
    "supplier_level",
    "intent_level_tag_id",
    "intent_level_code",
    "hangup_by",
    "chat_round",
    "ring_duration",
    "analysis_basis",
    "customer_concern",
    "record_url",
    "record_expire_at",
    "asr_transcript",
    "first_message",
    "last_message",
    "properties",
    "dynamic_properties",
    "callback_data_type",
    "raw_json",
)

_JSON_COLUMNS = {"properties", "dynamic_properties", "raw_json"}

# 外部报文可能超长，按表结构长度截断，避免严格模式写入失败
_MAX_LENGTHS = {
    "record_id": 64,
    "credit_code": 32,
    "customer_name": 128,
    "customer_phone": 32,
    "local_job_no": 32,
    "provider_job_id": 64,
    "call_status": 24,
    "status_raw": 64,
    "supplier_level": 64,
    "hangup_by": 24,
    "analysis_basis": 512,
    "customer_concern": 512,
    "record_url": 512,
    "first_message": 256,
    "last_message": 256,
    "callback_data_type": 64,
}

_UPSERT_SQL = f"""
INSERT INTO `call_record` ({", ".join(f"`{c}`" for c in _COLUMNS)})
VALUES ({", ".join(["%s"] * len(_COLUMNS))})
ON DUPLICATE KEY UPDATE
  `credit_code` = COALESCE(VALUES(`credit_code`), `credit_code`),
  `customer_name` = COALESCE(NULLIF(VALUES(`customer_name`), ''), `customer_name`),
  `customer_phone` = VALUES(`customer_phone`),
  `local_job_no` = COALESCE(NULLIF(VALUES(`local_job_no`), ''), `local_job_no`),
  `provider_job_id` = COALESCE(NULLIF(VALUES(`provider_job_id`), ''), `provider_job_id`),
  `call_time` = VALUES(`call_time`),
  `duration` = VALUES(`duration`),
  `call_status` = VALUES(`call_status`),
  `status_raw` = COALESCE(NULLIF(VALUES(`status_raw`), ''), `status_raw`),
  `supplier_level` = COALESCE(NULLIF(VALUES(`supplier_level`), ''), `supplier_level`),
  `intent_level_tag_id` = COALESCE(VALUES(`intent_level_tag_id`), `intent_level_tag_id`),
  `intent_level_code` = COALESCE(VALUES(`intent_level_code`), `intent_level_code`),
  `hangup_by` = COALESCE(NULLIF(VALUES(`hangup_by`), ''), `hangup_by`),
  `chat_round` = COALESCE(VALUES(`chat_round`), `chat_round`),
  `ring_duration` = COALESCE(VALUES(`ring_duration`), `ring_duration`),
  `analysis_basis` = COALESCE(NULLIF(VALUES(`analysis_basis`), ''), `analysis_basis`),
  `customer_concern` = COALESCE(NULLIF(VALUES(`customer_concern`), ''), `customer_concern`),
  `record_url` = COALESCE(NULLIF(VALUES(`record_url`), ''), `record_url`),
  `record_expire_at` = COALESCE(VALUES(`record_expire_at`), `record_expire_at`),
  `asr_transcript` = COALESCE(NULLIF(VALUES(`asr_transcript`), ''), `asr_transcript`),
  `first_message` = COALESCE(NULLIF(VALUES(`first_message`), ''), `first_message`),
  `last_message` = COALESCE(NULLIF(VALUES(`last_message`), ''), `last_message`),
  `properties` = COALESCE(VALUES(`properties`), `properties`),
  `dynamic_properties` = COALESCE(VALUES(`dynamic_properties`), `dynamic_properties`),
  `callback_data_type` = VALUES(`callback_data_type`),
  `raw_json` = VALUES(`raw_json`),
  `synced_at` = CURRENT_TIMESTAMP
"""


def normalize_phone(phone: str) -> str:
    """去掉空格、横线与全角空格，便于号码精确匹配。"""
    text = str(phone or "")
    for ch in (" ", "\t", "\u3000", "-", "－"):
        text = text.replace(ch, "")
    return text.strip()


def find_credit_code_by_phone(phone: str) -> str | None:
    """按号码反查企业信用代码；查不到返回 None。"""
    compact = normalize_phone(phone)
    if not compact:
        return None
    sql = (
        "SELECT `credit_code` FROM `customer_contact` "
        "WHERE `phone` = %s "
        "   OR REPLACE(REPLACE(REPLACE(`phone`, '-', ''), ' ', ''), '　', '') = %s "
        "ORDER BY `is_primary` DESC, `id` "
        "LIMIT 1"
    )
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (phone, compact))
            row = cur.fetchone()
            return str(row["credit_code"]) if row and row.get("credit_code") else None
    except pymysql.MySQLError as exc:
        log.warning("按号码反查企业失败：%s", exc)
        return None


def _serialize(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)
    if column in ("call_time",) and isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if column in ("record_expire_at",) and hasattr(value, "isoformat"):
        return value.isoformat()
    limit = _MAX_LENGTHS.get(column)
    if limit and isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def upsert_call_record(row: dict) -> None:
    """按 record_id 幂等写入；重复回调只更新，不新增。"""
    values = [_serialize(column, row.get(column)) for column in _COLUMNS]
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_UPSERT_SQL, values)
        # pymysql 的连接上下文只负责关闭，不自动提交，必须显式 commit
        conn.commit()
    except pymysql.MySQLError as exc:
        conn.rollback()
        log.error("写入 call_record 失败 record_id=%s：%s", row.get("record_id"), exc)
        raise
    finally:
        conn.close()


def get_call_record(record_id: str) -> dict | None:
    """按一知通话记录 ID 查询本地记录。"""
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `call_record` WHERE `record_id` = %s LIMIT 1",
                (str(record_id),),
            )
            return cur.fetchone()
    except pymysql.MySQLError as exc:
        log.warning("查询 call_record 失败 record_id=%s：%s", record_id, exc)
        return None


def count_call_records(record_id: str) -> int:
    """统计同一 record_id 的行数，用于验证回调幂等。"""
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS total FROM `call_record` WHERE `record_id` = %s",
                (str(record_id),),
            )
            row = cur.fetchone()
            return int(row["total"]) if row else 0
    except pymysql.MySQLError as exc:
        log.warning("统计 call_record 失败 record_id=%s：%s", record_id, exc)
        return 0


def get_local_job_no_by_provider_job(provider_job_id: str | None) -> str | None:
    """按一知任务 ID 反查我方任务编号。"""
    provider_job_id = str(provider_job_id or "").strip()
    if not provider_job_id:
        return None
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `local_job_no` FROM `outbound_job` "
                "WHERE `provider` = 'YIZHI' AND `provider_job_id` = %s LIMIT 1",
                (provider_job_id,),
            )
            row = cur.fetchone()
            return str(row["local_job_no"]) if row and row.get("local_job_no") else None
    except pymysql.MySQLError as exc:
        log.warning("反查 outbound_job 失败 provider_job_id=%s：%s", provider_job_id, exc)
        return None
    finally:
        conn.close()
