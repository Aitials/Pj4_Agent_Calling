"""飞书成员通讯录本地缓存数据访问层。"""

from __future__ import annotations

import json

import pymysql

from app.repositories.customer_repo import DatabaseError, connect

MEMBER_COLUMNS = ["open_id", "union_id", "name", "en_name", "department_names", "is_deleted"]

_UPSERT_SQL = """
INSERT INTO `feishu_user`
  (`open_id`, `union_id`, `name`, `en_name`, `mobile_visible`, `is_agent`,
   `status`, `is_deleted`, `synced_at`, `raw_json`)
VALUES
  (%(open_id)s, %(union_id)s, %(name)s, %(en_name)s, %(mobile_visible)s, %(is_agent)s,
   %(status)s, 0, NOW(), %(raw_json)s)
ON DUPLICATE KEY UPDATE
  `union_id`       = VALUES(`union_id`),
  `name`           = VALUES(`name`),
  `en_name`        = VALUES(`en_name`),
  `mobile_visible` = VALUES(`mobile_visible`),
  `is_agent`       = VALUES(`is_agent`),
  `status`         = VALUES(`status`),
  `is_deleted`     = 0,
  `synced_at`      = NOW(),
  `raw_json`       = VALUES(`raw_json`)
"""


def _wrap_db_error(exc: pymysql.MySQLError, action: str) -> DatabaseError:
    if isinstance(exc, pymysql.err.ProgrammingError) and exc.args and exc.args[0] == 1146:
        return DatabaseError("数据表 feishu_user 不存在，请先执行 sql/002_create_feishu_user.sql")
    return DatabaseError(f"{action}失败：{exc}")


def upsert_users(users: list[dict]) -> int:
    """批量写入/更新成员，返回处理条数。open_id 冲突时覆盖字段并恢复 is_deleted=0。"""
    if not users:
        return 0

    rows = []
    for user in users:
        open_id = (user.get("open_id") or "").strip()
        if not open_id:
            continue
        raw = user.get("raw") or {}
        rows.append(
            {
                "open_id": open_id,
                "union_id": user.get("union_id") or None,
                "name": user.get("name") or None,
                "en_name": user.get("en_name") or None,
                "mobile_visible": 1 if user.get("mobile_visible") else 0,
                "is_agent": 1 if user.get("is_agent") else 0,
                "status": int(user.get("status") or 0),
                "raw_json": (
                    json.dumps(raw, ensure_ascii=False, default=str) if raw else None
                ),
            }
        )

    if not rows:
        return 0

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT_SQL, rows)
        conn.commit()
        return len(rows)
    except pymysql.MySQLError as exc:
        conn.rollback()
        raise _wrap_db_error(exc, "写入通讯录") from exc
    finally:
        conn.close()


def mark_missing_deleted(active_open_ids: list[str]) -> int:
    """把本次未同步到的成员标记为已删除，返回影响行数。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            if not active_open_ids:
                cur.execute("UPDATE `feishu_user` SET `is_deleted` = 1 WHERE `is_deleted` = 0")
            else:
                placeholders = ", ".join(["%s"] * len(active_open_ids))
                cur.execute(
                    f"UPDATE `feishu_user` SET `is_deleted` = 1 "
                    f"WHERE `is_deleted` = 0 AND `open_id` NOT IN ({placeholders})",
                    tuple(active_open_ids),
                )
            affected = cur.rowcount
        conn.commit()
        return affected
    except pymysql.MySQLError as exc:
        conn.rollback()
        raise _wrap_db_error(exc, "标记离职成员") from exc
    finally:
        conn.close()


def search_members(keyword: str, limit: int = 10) -> list[dict]:
    """按姓名模糊或 open_id 精确查询在职成员。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("查询关键字不能为空")

    limit = max(1, min(int(limit or 10), 20))
    columns = ", ".join(f"`{col}`" for col in MEMBER_COLUMNS)

    if keyword.startswith("ou_"):
        where = "`open_id` = %s"
        params: tuple = (keyword,)
    else:
        where = "`name` LIKE %s"
        params = (f"%{keyword}%",)

    sql = (
        f"SELECT {columns} FROM `feishu_user` "
        f"WHERE `is_deleted` = 0 AND {where} "
        "ORDER BY `name` LIMIT %s"
    )

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (*params, limit))
            return list(cur.fetchall())
    except pymysql.MySQLError as exc:
        raise _wrap_db_error(exc, "查询通讯录") from exc


def get_member(open_id: str) -> dict | None:
    """按 open_id 查单个成员。"""
    open_id = (open_id or "").strip()
    if not open_id:
        return None

    columns = ", ".join(f"`{col}`" for col in MEMBER_COLUMNS)
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {columns} FROM `feishu_user` WHERE `open_id` = %s",
                (open_id,),
            )
            return cur.fetchone()
    except pymysql.MySQLError as exc:
        raise _wrap_db_error(exc, "查询成员") from exc


def count_members() -> int:
    """统计在职成员数量。"""
    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM `feishu_user` WHERE `is_deleted` = 0")
            row = cur.fetchone() or {}
            return int(row.get("c") or 0)
    except pymysql.MySQLError as exc:
        raise _wrap_db_error(exc, "统计成员") from exc
