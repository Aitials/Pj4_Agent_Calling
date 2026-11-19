"""lead_brief 数据分析任务队列：入队、抢占、状态流转。"""

from __future__ import annotations

import logging

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("lead_brief_repo")

CLAIMABLE = ("PENDING", "RETRY")


def enqueue(
    record_id: str,
    credit_code: str | None,
    intent_code: int | None,
    conn=None,
) -> int | None:
    """按 record_id 幂等入队；新建返回 id，已存在返回 None。

    传入 conn 时复用调用方事务，不提交也不关闭。
    """
    sql = (
        "INSERT INTO `lead_brief` (`record_id`, `credit_code`, `intent_code`, `status`) "
        "VALUES (%s, %s, %s, 'PENDING') "
        "ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`)"
    )
    own_conn = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (str(record_id), credit_code, intent_code))
            lead_id = cur.lastrowid
            created = cur.rowcount == 1
        if own_conn:
            conn.commit()
        return int(lead_id) if created and lead_id else None
    except pymysql.MySQLError:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()


def claim_pending(limit: int, worker_id: str) -> list[dict]:
    """领取待分析任务并锁定，避免多进程重复执行。"""
    limit = max(1, min(int(limit or 10), 100))
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `lead_brief` "
                "WHERE `status` IN ('PENDING', 'RETRY') "
                "  AND (`next_retry_at` IS NULL OR `next_retry_at` <= NOW()) "
                "ORDER BY `created_at`, `id` LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            )
            rows = list(cur.fetchall())
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ", ".join(["%s"] * len(ids))
                cur.execute(
                    "UPDATE `lead_brief` SET `status` = 'PROCESSING', "
                    "  `locked_at` = NOW(), `worker_id` = %s, `updated_at` = NOW() "
                    f"WHERE `id` IN ({placeholders})",
                    (worker_id, *ids),
                )
        conn.commit()
        return rows
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_done(lead_id: int, analysis_id: int) -> None:
    _execute(
        "UPDATE `lead_brief` "
        "SET `status` = 'DONE', `analysis_id` = %s, `last_error` = NULL, "
        "    `next_retry_at` = NULL, `locked_at` = NULL, `updated_at` = NOW() "
        "WHERE `id` = %s",
        (int(analysis_id), int(lead_id)),
    )


def mark_retry(
    lead_id: int,
    error_msg: str,
    retry_count: int,
    next_retry_at,
    max_retry: int = 3,
) -> None:
    """失败回写：未超限置 RETRY，超限置 FAILED。"""
    status = "RETRY" if int(retry_count) < max(1, int(max_retry)) else "FAILED"
    _execute(
        "UPDATE `lead_brief` "
        "SET `status` = %s, `retry_count` = %s, `last_error` = %s, "
        "    `next_retry_at` = %s, `locked_at` = NULL, `updated_at` = NOW() "
        "WHERE `id` = %s",
        (status, int(retry_count), str(error_msg)[:2000], next_retry_at, int(lead_id)),
    )


def reclaim_timeout(minutes: int = 10, max_retry: int = 3) -> int:
    """回收执行超时的 PROCESSING 任务，放回 RETRY 或置 FAILED。"""
    minutes = max(1, int(minutes or 10))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `id`, `retry_count` FROM `lead_brief` "
                "WHERE `status` = 'PROCESSING' "
                f"  AND `locked_at` < NOW() - INTERVAL {minutes} MINUTE"
            )
            rows = list(cur.fetchall())
            for row in rows:
                retry = int(row.get("retry_count") or 0) + 1
                status = "RETRY" if retry < max(1, int(max_retry)) else "FAILED"
                cur.execute(
                    "UPDATE `lead_brief` "
                    "SET `status` = %s, `retry_count` = %s, `last_error` = %s, "
                    "    `next_retry_at` = NOW(), `locked_at` = NULL, `updated_at` = NOW() "
                    "WHERE `id` = %s",
                    (status, retry, "执行超时，任务被回收", row["id"]),
                )
        conn.commit()
        return len(rows)
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_by_record(record_id: str) -> dict | None:
    return _fetch_one(
        "SELECT * FROM `lead_brief` WHERE `record_id` = %s LIMIT 1",
        (str(record_id),),
    )


def get_by_id(lead_id: int) -> dict | None:
    return _fetch_one("SELECT * FROM `lead_brief` WHERE `id` = %s LIMIT 1", (int(lead_id),))


def _fetch_one(sql: str, params: tuple) -> dict | None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _execute(sql: str, params: tuple) -> None:
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
