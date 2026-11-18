"""数据同步日志：拉取游标与每轮执行结果。"""

from __future__ import annotations

import logging
from datetime import datetime

import pymysql

from app.repositories.customer_repo import connect

log = logging.getLogger("sync_log_repo")

PROVIDER = "YIZHI"


def get_cursor(interface_name: str) -> datetime | None:
    """取上次成功同步的最大业务时间。"""
    sql = (
        "SELECT `cursor_time` FROM `sync_log` "
        "WHERE `provider` = %s AND `interface_name` = %s "
        "  AND `status` IN ('SUCCESS', 'PARTIAL') AND `cursor_time` IS NOT NULL "
        "ORDER BY `id` DESC LIMIT 1"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (PROVIDER, interface_name))
            row = cur.fetchone()
            return row.get("cursor_time") if row else None
    finally:
        conn.close()


def start_run(interface_name: str) -> int:
    sql = (
        "INSERT INTO `sync_log` "
        "  (`provider`, `interface_name`, `sync_start_time`, `status`) "
        "VALUES (%s, %s, NOW(), 'RUNNING')"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (PROVIDER, interface_name))
            run_id = cur.lastrowid
        conn.commit()
        return int(run_id)
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_run(
    run_id: int,
    *,
    status: str,
    fetched: int = 0,
    saved: int = 0,
    cursor_time: datetime | None = None,
    error_msg: str | None = None,
) -> None:
    sql = (
        "UPDATE `sync_log` SET "
        "  `sync_end_time` = NOW(), `records_fetched` = %s, `records_saved` = %s, "
        "  `status` = %s, `cursor_time` = %s, `error_msg` = %s "
        "WHERE `id` = %s"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (int(fetched), int(saved), status, cursor_time, (error_msg or "")[:512] or None, int(run_id)),
            )
        conn.commit()
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()
