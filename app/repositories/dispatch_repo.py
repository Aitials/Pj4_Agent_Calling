"""销售通知队列与轮流分配数据访问。"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pymysql

from app.repositories.customer_repo import connect, get_company_name

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


def next_assignee(conn=None) -> dict | None:
    """按 sort_order 轮转取下一个销售；名单为空返回 None。

    传入 conn 时复用调用方事务（与通知入队同一事务，避免游标已提交但入队失败），
    此时不 begin / commit / close。
    """
    if conn is not None:
        return _next_assignee_locked(conn)

    own_conn = connect()
    try:
        own_conn.begin()
        chosen = _next_assignee_locked(own_conn)
        own_conn.commit()
        return chosen
    except pymysql.MySQLError:
        own_conn.rollback()
        raise
    finally:
        own_conn.close()


def _next_assignee_locked(conn) -> dict | None:
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
    return chosen


def enqueue_notice(notice: dict, conn=None) -> int | None:
    """写入通知队列，返回新建的 notice_id。

    唯一键是 trigger_record_id（一通电话一条）；已存在时返回 None。
    传入 conn 时复用调用方事务，不提交也不关闭。
    """
    sql = """
    INSERT INTO `dispatch_notice`
      (`credit_code`, `company_name`, `trigger_record_id`, `trigger_phone`,
       `assignee_open_id`, `assignee_name`, `trigger_source`, `status`,
       `analysis_status`, `notice_date`)
    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING', 'PENDING', %s)
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
    own_conn = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, values)
            notice_id = cur.lastrowid
            created = cur.rowcount == 1
        if own_conn:
            conn.commit()
        if not created:
            return None
        return int(notice_id) if notice_id else None
    except pymysql.MySQLError as exc:
        if own_conn:
            conn.rollback()
        log.error("写入 dispatch_notice 失败：%s", exc)
        raise
    finally:
        if own_conn:
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
                    f"UPDATE `dispatch_notice` SET `status` = 'SENDING', "
                    f"    `sending_at` = NOW() "
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
    """把长时间停留在 SENDING 的记录放回 PENDING（进程异常退出后的恢复）。

    必须按「抢占时间」回收：created_at 是入队时间，用它会把刚被抢占的老记录
    立即放回 PENDING，导致其他 Worker 重复发送。存量行 sending_at 为空时，
    退回 updated_at / created_at 兼容。
    """
    minutes = max(1, int(minutes or 10))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` SET `status` = 'PENDING', `sending_at` = NULL "
                f"WHERE `status` = 'SENDING' "
                f"  AND COALESCE(`sending_at`, `updated_at`, `created_at`) "
                f"      < NOW() - INTERVAL {minutes} MINUTE"
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
    # 只允许 SENDING → SENT：若发送期间被 AI 判定撤销（CANCELLED），不覆盖撤销状态
    affected = _update(
        "UPDATE `dispatch_notice` "
        "SET `status` = 'SENT', `sent_at` = NOW(), `feishu_task_guid` = %s, "
        "    `card_message_id` = %s, `error_msg` = NULL, `next_retry_at` = NULL, "
        "    `sending_at` = NULL "
        "WHERE `id` = %s AND `status` = 'SENDING'",
        (task_guid, message_id, int(notice_id)),
    )
    if affected == 0:
        # 典型原因：被超时回收或被 AI 撤销；消息可能已经发出，必须显式告警
        log.warning(
            "通知 %s 已不处于 SENDING，mark_sent 未生效（可能被回收或撤销）",
            notice_id,
        )


def mark_failed(
    notice_id: int,
    error_msg: str,
    retry_count: int,
    next_retry_at: datetime | None,
    task_guid: str | None = None,
) -> None:
    """失败回写：还有重试机会就回到 PENDING，否则置 FAILED。

    之前固定写 FAILED，而 claim_pending 只扫 PENDING，导致自动重试永远不生效。
    """
    status = "PENDING" if next_retry_at else "FAILED"
    affected = _update(
        "UPDATE `dispatch_notice` "
        "SET `status` = %s, `error_msg` = %s, `retry_count` = %s, "
        "    `next_retry_at` = %s, `sending_at` = NULL, "
        "    `feishu_task_guid` = COALESCE(%s, `feishu_task_guid`) "
        "WHERE `id` = %s AND `status` = 'SENDING'",
        (status, error_msg[:512], int(retry_count), next_retry_at, task_guid, int(notice_id)),
    )
    if affected == 0:
        log.warning(
            "通知 %s 已不处于 SENDING，mark_failed 未生效（可能被回收或撤销）",
            notice_id,
        )


def reset_failed() -> int:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` "
                "SET `status` = 'PENDING', `retry_count` = 0, "
                "    `next_retry_at` = NULL, `error_msg` = NULL, `sending_at` = NULL "
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


def claim_analysis_pending(limit: int = 20, max_retry: int = 3) -> list[dict]:
    """抢占待发送的分析结果通知（基础通知必须已 SENT）。"""
    limit = max(1, min(int(limit or 20), 100))
    max_retry = max(1, int(max_retry or 3))
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `dispatch_notice` "
                "WHERE `analysis_status` = 'PENDING' "
                "  AND `status` = 'SENT' "
                "  AND `analysis_retry_count` < %s "
                "ORDER BY `id` LIMIT %s FOR UPDATE SKIP LOCKED",
                (max_retry, limit),
            )
            rows = list(cur.fetchall())
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ", ".join(["%s"] * len(ids))
                cur.execute(
                    f"UPDATE `dispatch_notice` SET `analysis_status` = 'SENDING', "
                    f"    `updated_at` = NOW() "
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


def reclaim_analysis_sending(minutes: int = 10) -> int:
    """把长时间停留在 SENDING 的分析通知放回 PENDING。

    必须按「抢占时间」回收：claim_analysis_pending 会显式把 updated_at 置为 NOW()，
    因此这里用 updated_at 就是抢占时刻。若用 created_at / 旧 updated_at，
    刚被抢占的老记录会被立即放回 PENDING，导致重复发送分析卡片。
    """
    minutes = max(1, int(minutes or 10))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` SET `analysis_status` = 'PENDING', "
                "  `updated_at` = NOW() "
                f"WHERE `analysis_status` = 'SENDING' "
                f"  AND `updated_at` < NOW() - INTERVAL {minutes} MINUTE"
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_analysis_pending(record_id: str) -> None:
    """分析完成后把对应通知置为待发送；已发送的不回退。"""
    record_id = (record_id or "").strip()
    if not record_id:
        return
    _update(
        "UPDATE `dispatch_notice` SET `analysis_status` = 'PENDING' "
        "WHERE `trigger_record_id` = %s AND `analysis_status` IS NULL",
        (record_id,),
    )


def mark_analysis_sending(notice_id: int) -> None:
    # 只允许 PENDING/FAILED → SENDING：已 SENT 或已被撤销的不可被重新抢占
    affected = _update(
        "UPDATE `dispatch_notice` SET `analysis_status` = 'SENDING', `updated_at` = NOW() "
        "WHERE `id` = %s AND `analysis_status` IN ('PENDING', 'FAILED')",
        (int(notice_id),),
    )
    if affected == 0:
        log.warning("通知 %s 不在 PENDING/FAILED，mark_analysis_sending 未生效", notice_id)


def mark_analysis_sent(notice_id: int, card_message_id: str | None = None) -> None:
    # 只允许 SENDING → SENT：被超时回收或撤销后不回退覆盖
    if card_message_id:
        affected = _update(
            "UPDATE `dispatch_notice` "
            "SET `analysis_status` = 'SENT', `card_message_id` = %s, `updated_at` = NOW() "
            "WHERE `id` = %s AND `analysis_status` = 'SENDING'",
            (card_message_id, int(notice_id)),
        )
    else:
        affected = _update(
            "UPDATE `dispatch_notice` "
            "SET `analysis_status` = 'SENT', `updated_at` = NOW() "
            "WHERE `id` = %s AND `analysis_status` = 'SENDING'",
            (int(notice_id),),
        )
    if affected == 0:
        log.warning(
            "通知 %s 已不处于 SENDING，mark_analysis_sent 未生效（可能被回收或撤销）",
            notice_id,
        )


def mark_analysis_failed(
    notice_id: int,
    error_msg: str,
    retry_count: int,
    max_retry: int = 3,
) -> None:
    """分析通知失败：未超限回到 PENDING，超限置 FAILED。"""
    status = "PENDING" if int(retry_count) < max(1, int(max_retry)) else "FAILED"
    affected = _update(
        "UPDATE `dispatch_notice` "
        "SET `analysis_status` = %s, `analysis_retry_count` = %s, `error_msg` = %s, "
        "    `updated_at` = NOW() "
        "WHERE `id` = %s AND `analysis_status` = 'SENDING'",
        (status, int(retry_count), str(error_msg)[:512], int(notice_id)),
    )
    if affected == 0:
        log.warning(
            "通知 %s 已不处于 SENDING，mark_analysis_failed 未生效（可能被回收或撤销）",
            notice_id,
        )


def mark_attachment_pending(notice_id: int) -> None:
    _update(
        "UPDATE `dispatch_notice` SET `attachment_status` = 'PENDING', "
        "  `attachment_updated_at` = NOW() WHERE `id` = %s",
        (int(notice_id),),
    )


def mark_attachment_skipped(notice_id: int, reason: str = "") -> None:
    _update(
        "UPDATE `dispatch_notice` SET `attachment_status` = 'SKIPPED', "
        "  `attachment_error` = %s, `attachment_updated_at` = NOW() WHERE `id` = %s",
        (str(reason)[:512] or None, int(notice_id)),
    )


def claim_attachment_pending(limit: int = 10, max_retry: int = 3) -> list[dict]:
    """抢占待上传附件的通知；要求卡片已发送且飞书任务已创建。"""
    limit = max(1, min(int(limit or 10), 100))
    max_retry = max(1, int(max_retry or 3))
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `dispatch_notice` "
                "WHERE `attachment_status` IN ('PENDING', 'FAILED') "
                "  AND `attachment_retry_count` < %s "
                "  AND `analysis_status` = 'SENT' "
                "  AND `feishu_task_guid` IS NOT NULL "
                "ORDER BY `id` LIMIT %s FOR UPDATE SKIP LOCKED",
                (max_retry, limit),
            )
            rows = list(cur.fetchall())
            if rows:
                ids = [row["id"] for row in rows]
                placeholders = ", ".join(["%s"] * len(ids))
                cur.execute(
                    "UPDATE `dispatch_notice` SET `attachment_status` = 'UPLOADING', "
                    "  `attachment_updated_at` = NOW() "
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


def reclaim_attachment_uploading(minutes: int = 10) -> int:
    """把长时间停留在 UPLOADING 的附件任务放回 PENDING。"""
    minutes = max(1, int(minutes or 10))
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE `dispatch_notice` SET `attachment_status` = 'PENDING' "
                "WHERE `attachment_status` = 'UPLOADING' "
                f"  AND `attachment_updated_at` < NOW() - INTERVAL {minutes} MINUTE"
            )
            affected = cur.rowcount
        conn.commit()
        return affected
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_attachment_guid(
    notice_id: int,
    guid: str,
    url: str | None,
    local_path: str | None,
) -> None:
    """上传成功后立即记录 GUID，后续重试据此跳过重复上传。"""
    _update(
        "UPDATE `dispatch_notice` SET `attachment_guid` = %s, `attachment_url` = %s, "
        "  `attachment_local_path` = %s, `attachment_updated_at` = NOW() WHERE `id` = %s",
        (guid, url, local_path, int(notice_id)),
    )


def mark_attachment_done(notice_id: int) -> None:
    _update(
        "UPDATE `dispatch_notice` SET `attachment_status` = 'DONE', "
        "  `attachment_error` = NULL, `attachment_updated_at` = NOW() WHERE `id` = %s",
        (int(notice_id),),
    )


def mark_attachment_failed(
    notice_id: int,
    error_msg: str,
    retry_count: int,
    max_retry: int = 3,
) -> None:
    """附件失败只影响附件状态，卡片状态保持不变。"""
    status = "FAILED" if int(retry_count) >= max(1, int(max_retry)) else "PENDING"
    _update(
        "UPDATE `dispatch_notice` SET `attachment_status` = %s, "
        "  `attachment_retry_count` = %s, `attachment_error` = %s, "
        "  `attachment_updated_at` = NOW() WHERE `id` = %s",
        (status, int(retry_count), str(error_msg)[:512], int(notice_id)),
    )


def get_notice(notice_id: int) -> dict | None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM `dispatch_notice` WHERE `id` = %s LIMIT 1", (int(notice_id),))
            return cur.fetchone()
    finally:
        conn.close()


def get_notice_by_record(record_id: str, conn=None, for_update: bool = False) -> dict | None:
    """同一通电话是否已经入过队，避免重复轮转销售。

    传入 conn 时复用调用方事务，不提交也不关闭。
    for_update=True 时用加锁读：在入库事务里先锁住该 record_id 的索引间隙，
    并发回调只能有一个走到轮转与入队。
    """
    record_id = (record_id or "").strip()
    if not record_id:
        return None
    own_conn = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM `dispatch_notice` "
                "WHERE `trigger_record_id` = %s ORDER BY `id` LIMIT 1"
                + (" FOR UPDATE" if for_update else ""),
                (record_id,),
            )
            return cur.fetchone()
    finally:
        if own_conn:
            conn.close()


def cancel_notice(record_id: str, reason: str = "") -> str | None:
    """AI 否决派单时把通知置为 CANCELLED，返回撤销前的状态；无通知返回 None。

    撤销后基础通知不会被 claim_pending 再发送，分析卡片与附件也一并停发。
    """
    record_id = (record_id or "").strip()
    if not record_id:
        return None
    conn = connect()
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `id`, `status` FROM `dispatch_notice` "
                "WHERE `trigger_record_id` = %s ORDER BY `id` LIMIT 1 FOR UPDATE",
                (record_id,),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            cur.execute(
                "UPDATE `dispatch_notice` "
                "SET `status` = 'CANCELLED', `error_msg` = %s, "
                "    `analysis_status` = CASE WHEN `analysis_status` IN ('PENDING', 'SENDING') "
                "                             THEN NULL ELSE `analysis_status` END, "
                "    `attachment_status` = CASE WHEN `attachment_status` IN ('PENDING', 'UPLOADING', 'FAILED') "
                "                               THEN 'SKIPPED' ELSE `attachment_status` END, "
                "    `updated_at` = NOW() "
                "WHERE `id` = %s",
                (str(reason)[:512] or None, int(row["id"])),
            )
        conn.commit()
        return str(row.get("status") or "")
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()


def enqueue_ai_notice(record: dict) -> int | None:
    """供应商未触发但 AI 判为高意向时补派单，trigger_source 记为 AI。"""
    record_id = str(record.get("record_id") or "").strip()
    credit_code = str(record.get("credit_code") or "").strip()
    if not record_id or not credit_code:
        log.warning("通话 %s 缺少信用代码，无法补派单", record_id)
        return None
    if get_notice_by_record(record_id):
        return None
    assignee = next_assignee()
    if not assignee:
        log.warning("销售名单为空，通话 %s 无法补派单", record_id)
        return None

    notice = {
        "credit_code": credit_code,
        "company_name": record.get("customer_name") or get_company_name(credit_code),
        "trigger_record_id": record_id,
        "trigger_phone": record.get("customer_phone"),
        "assignee_open_id": assignee["open_id"],
        "assignee_name": assignee.get("name"),
        "trigger_source": "AI",
        "notice_date": date.today(),
    }
    return enqueue_notice(notice)


def _update(sql: str, params: tuple) -> int:
    """执行单条 UPDATE，返回受影响行数（0 表示状态前置条件不满足）。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            affected = cur.rowcount
        conn.commit()
        return int(affected or 0)
    except pymysql.MySQLError:
        conn.rollback()
        raise
    finally:
        conn.close()
