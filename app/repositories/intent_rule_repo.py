"""意向等级本地分级映射数据访问。"""

from __future__ import annotations

import logging

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("intent_rule_repo")

_UPSERT_SQL = """
INSERT INTO `intent_level_rule`
  (`intent_level_tag_id`, `intent_level_code`, `code_name`, `local_level`, `notify`, `enabled`)
VALUES (%s, %s, %s, %s, %s, 1)
ON DUPLICATE KEY UPDATE
  `code_name` = VALUES(`code_name`),
  `updated_at` = CURRENT_TIMESTAMP
"""


def upsert_rules(tag_id: int, rows: list[dict]) -> int:
    """同步标签名称；已存在的行保留人工设置的 local_level / notify。"""
    values = [
        (
            int(tag_id),
            int(row["code"]),
            str(row["name"])[:64],
            str(row.get("local_level") or "NONE")[:8],
            1 if row.get("notify") else 0,
        )
        for row in rows
        if row.get("code") is not None and row.get("name")
    ]
    if not values:
        return 0

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, values)
        conn.commit()
        return len(values)
    except pymysql.MySQLError as exc:
        conn.rollback()
        log.error("写入 intent_level_rule 失败：%s", exc)
        raise
    finally:
        conn.close()


def get_rule(tag_id: int | None, code: int | None) -> dict | None:
    """按标签组 + code 查启用中的规则。"""
    if tag_id is None or code is None:
        return None
    sql = (
        "SELECT `intent_level_tag_id`, `intent_level_code`, `code_name`, "
        "       `local_level`, `notify`, `enabled` "
        "FROM `intent_level_rule` "
        "WHERE `intent_level_tag_id` = %s AND `intent_level_code` = %s AND `enabled` = 1 "
        "LIMIT 1"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (int(tag_id), int(code)))
            return cur.fetchone()
    finally:
        conn.close()


def list_rules(tag_id: int) -> list[dict]:
    sql = (
        "SELECT `intent_level_code`, `code_name`, `local_level`, `notify`, `enabled` "
        "FROM `intent_level_rule` WHERE `intent_level_tag_id` = %s "
        "ORDER BY `intent_level_code`"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (int(tag_id),))
            return list(cur.fetchall())
    finally:
        conn.close()
