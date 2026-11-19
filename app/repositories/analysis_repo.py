"""agent_analysis（客户分析结果）数据访问：版本化写入与查询。"""

from __future__ import annotations

import json
import logging
from typing import Any

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("analysis_repo")

_JSON_FIELDS = (
    "customer_needs",
    "concerns",
    "objections",
    "intent_evidence",
    "value_points",
    "objection_handling",
    "raw_json",
)


def save_analysis(
    *,
    lead_brief_id: int | None,
    record_id: str,
    credit_code: str,
    customer_phone: str,
    intent_level: str,
    reason: str = "",
    quote: str = "",
    demand: str = "",
    budget: str = "",
    objection: str = "",
    blocked: int = 0,
    block_reason: str = "",
    suggestion: str = "",
    best_follow_at: str = "",
    supplier_level: str = "",
    level_consistent: int | None = None,
    customer_needs: list[str] | None = None,
    concerns: list[str] | None = None,
    objections: list[str] | None = None,
    intent_evidence: list[str] | None = None,
    opening: str = "",
    value_points: list[str] | None = None,
    objection_handling: list[str] | None = None,
    next_action: str = "",
    model: str = "",
    raw_json: dict | None = None,
) -> int:
    """写入一条分析结果，自动分配 version，返回 analysis_id。

    同一 record_id 重新分析会追加新版本，不覆盖历史结果。
    """
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(`version`), 0) + 1 AS next_version "
                "FROM `agent_analysis` WHERE `record_id` = %s",
                (str(record_id),),
            )
            version = int((cur.fetchone() or {}).get("next_version") or 1)

            columns = [
                "lead_brief_id",
                "version",
                "record_id",
                "credit_code",
                "customer_phone",
                "intent_level",
                "reason",
                "quote",
                "demand",
                "budget",
                "objection",
                "blocked",
                "block_reason",
                "suggestion",
                "best_follow_at",
                "supplier_level",
                "level_consistent",
                "customer_needs",
                "concerns",
                "objections",
                "intent_evidence",
                "opening",
                "value_points",
                "objection_handling",
                "next_action",
                "model",
                "raw_json",
            ]
            values: list[Any] = [
                lead_brief_id,
                version,
                str(record_id),
                credit_code,
                customer_phone,
                intent_level,
                _text(reason, 512),
                _text(quote, 512),
                _text(demand, 512),
                _text(budget, 128),
                _text(objection, 512),
                int(blocked or 0),
                _text(block_reason, 64),
                _text(suggestion, 512),
                _text(best_follow_at, 64),
                _text(supplier_level, 16),
                level_consistent,
                _json(customer_needs),
                _json(concerns),
                _json(objections),
                _json(intent_evidence),
                opening or None,
                _json(value_points),
                _json(objection_handling),
                next_action or None,
                model or None,
                _json(raw_json),
            ]
            placeholders = ", ".join(["%s"] * len(columns))
            cur.execute(
                "INSERT INTO `agent_analysis` "
                f"({', '.join(f'`{c}`' for c in columns)}) VALUES ({placeholders})",
                values,
            )
            analysis_id = cur.lastrowid
        conn.commit()
        return int(analysis_id)
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current(record_id: str) -> dict | None:
    """取该通话的最新版本分析结果。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `agent_analysis` WHERE `record_id` = %s "
                "ORDER BY `version` DESC LIMIT 1",
                (str(record_id),),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_by_id(analysis_id: int) -> dict | None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `agent_analysis` WHERE `id` = %s LIMIT 1", (int(analysis_id),))
            return cur.fetchone()
    finally:
        conn.close()


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _text(value: str, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None
