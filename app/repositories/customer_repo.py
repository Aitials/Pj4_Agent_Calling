"""客户档案数据访问层。

只读查询本地 MySQL 的 customer_profile 表，连接信息取自 .env 的 MYSQL_*。
"""

from __future__ import annotations

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_env

PROFILE_COLUMNS = [
    "phone",
    "name",
    "company_name",
    "industry",
    "crm_cate",
    "phone_level",
    "call_times",
    "task_name",
    "note",
    "source_created_at",
]


class DatabaseError(RuntimeError):
    """数据库不可用（连接失败 / 表不存在 / 配置缺失）。"""


def _connection_config(database: str | None = None) -> dict:
    password = get_env("MYSQL_PASSWORD")
    if not password:
        raise DatabaseError("缺少 MYSQL_PASSWORD，请在 .env 中配置 MySQL 连接信息")
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip(),
        "password": password,
        "database": database or get_env("MYSQL_DATABASE").strip(),
        "charset": "utf8mb4",
        "connect_timeout": 8,
        "cursorclass": DictCursor,
    }


def connect(database: str | None = None):
    """建立数据库连接，配置缺失或连接失败时抛 DatabaseError。"""
    try:
        return pymysql.connect(**_connection_config(database))
    except pymysql.MySQLError as exc:
        raise DatabaseError(f"数据库连接失败：{exc}") from exc


def search_customers(keyword: str, limit: int = 10) -> list[dict]:
    """按手机号（全等）或姓名（模糊）查询客户档案。"""
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("查询关键字不能为空")

    limit = max(1, min(int(limit or 10), 50))
    is_phone = keyword.isdigit() and len(keyword) >= 6

    if is_phone:
        where = "`phone` = %s"
        params: tuple = (keyword,)
    else:
        where = "`name` LIKE %s"
        params = (f"%{keyword}%",)

    sql = (
        f"SELECT {', '.join(f'`{col}`' for col in PROFILE_COLUMNS)} "
        "FROM `customer_profile` "
        f"WHERE {where} "
        "ORDER BY `source_created_at` DESC, `id` DESC "
        "LIMIT %s"
    )

    conn = connect()

    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (*params, limit))
            return list(cur.fetchall())
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            raise DatabaseError("数据表 customer_profile 不存在，请先执行建表脚本") from exc
        raise DatabaseError(f"查询失败：{exc}") from exc
    except pymysql.MySQLError as exc:
        raise DatabaseError(f"查询失败：{exc}") from exc
