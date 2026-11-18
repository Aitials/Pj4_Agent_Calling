"""销售通知队列与轮流分配数据访问。"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("dispatch_repo")


def list_enabled_assignees() -> list[dict]:
    sql = (
        "SELECT `open_id`, `name` FROM `sales_assignee` "
        "WHERE `enabled` = 1 ORDER BY `sort_order`, `open_id`"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return list(cur.fetchall())
    finally:
        conn.close()


def next_assignee() -> dict | None:
    """按 sort_order 轮转取下一个销售；名单为空返回 None。"""
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("SELECT `last_open_id` FROM `dispatch_assignee_cursor` WHERE `id` = 1 FOR UPDATE")
            row = cur.fetchone() or {}
            last_open_id = row.get("last_open_id")

            cur.execute(
                "SELECT `open_id`, `name` FROM `sales_assignee` "
                "WHERE `enabled` = 1 ORDER BY `sort_order`, `open_id`"
            )
            assignees = list(cur.fetchall())
            if not assignees:
                conn.commit()
                return None

            open_ids = [item["open_id"] for item in assignees]
            index = open_ids.index(last_open_id) if last_open_id in open_ids else -1
            chosen = assignees[(index + 1) % len(assignees)]

            cur.execute(
                "INSERT INTO `dispatch_assignee_cursor` (`id`, `last_open_id`) "
                "VALUES (1, %s) "
                "ON DUPLICATE KEY UPDATE `last_open_id` = VALUES(`last_open_id`)",
                (chosen["open_id"],),
            )
        conn.commit()
        return chosen
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def enqueue_notice(notice: dict) -> int | None:
    """写入通知队列；(record_id, assignee) 冲突时不重复写入。"""
    sql = """
    INSERT INTO `dispatch_notice`
      (`credit_code`, `company_name`, `trigger_record_id`, `trigger_phone`,
       `assignee_open_id`, `assignee_name`, `trigger_source`, `status`, `notice_date`)
    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', %s)
    ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`)
    """
    values = (
        notice.get("credit_code"),
        notice.get("company_name"),
        notice.get("trigger_record_id"),
        notice.get("trigger_phone"),
        notice.get("assignee_open_id"),
        notice.get("assignee_name"),
        notice.get("trigger_source") or "SUPPLIER",
        notice.get("notice_date") or date.today(),
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, values)
            notice_id = cur.lastrowid
        conn.commit()
        return int(notice_id) if notice_id else None
    except pymysql.MySQLError as exc:
        conn.rollback()
        log.error("写入 dispatch_notice 失败：%s", exc)
        raise
    finally:
        conn.close()


def claim_pending(limit: int = 20) -> list[dict]:
    """抢占待发送通知，置为 SENDING；多进程下用 SKIP LOCKED 避免重复。"""
    limit = max(1, min(int(limit or 20), 100))
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `dispatch_notice` "
                "WHERE `status` = 'PENDING' "
                "  AND (`next_retry_at` IS NULL OR `next_retry_at` <= NOW()) "
                "ORDER BY `id` LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            rows = list(cur.fetchall())
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ", ".join(["%s"] * len(ids))
                cur.execute(
                    f"UPDATE `dispatch_notice` SET `status` = 'SENDING' "
                    f"WHERE `id` IN ({placeholders})",
                    tuple(ids),
                )
        conn.commit()
        return rows
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def reclaim_sending(minutes: int = 10) -> int:
    """把长时间停留在 SENDING 的记录放回 PENDING（进程异常退出后的恢复）。"""
    minutes = max(1, int(minutes or 10))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` SET `status` = 'PENDING' "
                f"WHERE `status` = 'SENDING' AND `created_at` < NOW() - INTERVAL {minutes} MINUTE"
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_task_guid(notice_id: int, task_guid: str) -> None:
    _update("UPDATE `dispatch_notice` SET `feishu_task_guid` = %s WHERE `id` = %s", (task_guid, int(notice_id)))


def mark_sent(notice_id: int, task_guid: str | None, message_id: str | None) -> None:
    _update(
        "UPDATE `dispatch_notice` "
        "SET `status` = 'SENT', `sent_at` = NOW(), `feishu_task_guid` = %s, "
        "    `card_message_id` = %s, `error_msg` = NULL, `next_retry_at` = NULL "
        "WHERE `id` = %s",
        (task_guid, message_id, int(notice_id)),
    )


def mark_failed(
    notice_id: int,
    error_msg: str,
    retry_count: int,
    next_retry_at: datetime | None,
    task_guid: str | None = None,
) -> None:
    _update(
        "UPDATE `dispatch_notice` "
        "SET `status` = 'FAILED', `error_msg` = %s, `retry_count` = %s, "
        "    `next_retry_at` = %s, `feishu_task_guid` = COALESCE(%s, `feishu_task_guid`) "
        "WHERE `id` = %s",
        (error_msg[:512], int(retry_count), next_retry_at, task_guid, int(notice_id)),
    )


def reset_failed() -> int:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` "
                "SET `status` = 'PENDING', `retry_count` = 0, "
                "    `next_retry_at` = NULL, `error_msg` = NULL "
                "WHERE `status` = 'FAILED'"
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_notice(notice_id: int) -> dict | None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `dispatch_notice` WHERE `id` = %s LIMIT 1", (int(notice_id),))
            return cur.fetchone()
    finally:
        conn.close()


def get_notice_by_record(record_id: str) -> dict | None:
    """同一通电话是否已经入过队，避免重复轮转销售。"""
    record_id = (record_id or "").strip()
    if not record_id:
        return None
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `dispatch_notice` "
                "WHERE `trigger_record_id` = %s ORDER BY `id` LIMIT 1",
                (record_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _update(sql: str, params: tuple) -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()
