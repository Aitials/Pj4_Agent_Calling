"""客户档案数据访问层。

查询本地 MySQL 的 customer_profile（企业主表）与 customer_contact（号码从表），
连接信息取自 .env 的 MYSQL_*。

查询优先级（依次兜底，前一档命中即返回）：
    1. 企业名称 company_name 模糊匹配
    2. 法人姓名 legal_person 模糊匹配
    3. 手机/固话 phone 精确匹配（关联从表反查企业）
"""

from __future__ import annotations

import re

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_env

PROFILE_COLUMNS = [
    "credit_code",
    "company_name",
    "legal_person",
    "registration_status",
    "industry",
    "econ_kind",
    "region",
    "province",
    "city",
    "address",
    "website",
    "registered_capital",
    "established_at",
    "insured_count",
    "contact_count",
    "call_times",
    "last_call_at",
    "best_intent_level",
    "last_intent_level",
    "is_blacklist",
    "note",
]

CONTACT_COLUMNS = [
    "credit_code",
    "phone",
    "phone_type",
    "phone_source",
    "contact_name",
    "is_primary",
    "is_blacklist",
    "block_reason",
    "call_times",
    "last_call_at",
    "last_call_status",
    "ai_intent_level",
]

_PHONE_PATTERN = re.compile(r"^[\d\-\s+]+$")


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


# ------------------------------------------------------------------ 内部工具


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % _ 被当成通配符。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_phone(keyword: str) -> str | None:
    """判断关键字是否像电话号码，是则返回去掉空格的号码，否则返回 None。"""
    text = (keyword or "").strip()
    if not _PHONE_PATTERN.match(text):
        return None
    compact = text.replace(" ", "").replace("\u3000", "")
    if sum(ch.isdigit() for ch in compact) < 6:
        return None
    return compact


def _raise_query_error(exc: Exception) -> None:
    """把 pymysql 异常统一翻译成 DatabaseError。"""
    if isinstance(exc, pymysql.err.ProgrammingError) and exc.args and exc.args[0] == 1146:
        raise DatabaseError("数据表不存在，请先执行建表脚本") from exc
    raise DatabaseError(f"查询失败：{exc}") from exc


def _query_profiles(where: str, params: tuple, limit: int) -> list[dict]:
    """按条件查企业主表。"""
    columns = ", ".join(f"`{col}`" for col in PROFILE_COLUMNS)
    sql = (
        f"SELECT {columns} "
        "FROM `customer_profile` "
        f"WHERE {where} "
        "ORDER BY `created_at` DESC, `id` DESC "
        "LIMIT %s"
    )

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (*params, limit))
            return list(cur.fetchall())
    except pymysql.MySQLError as exc:
        _raise_query_error(exc)
        return []


def _query_profiles_by_phone(phone: str, limit: int) -> list[dict]:
    """按号码精确匹配，关联从表反查企业。"""
    columns = ", ".join(f"p.`{col}`" for col in PROFILE_COLUMNS)
    sql = (
        f"SELECT {columns} "
        "FROM `customer_profile` AS p "
        "INNER JOIN `customer_contact` AS c ON c.`credit_code` = p.`credit_code` "
        "WHERE c.`phone` = %s OR REPLACE(c.`phone`, '-', '') = %s "
        "ORDER BY p.`created_at` DESC, p.`id` DESC "
        "LIMIT %s"
    )

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (phone, phone.replace("-", ""), limit))
            return list(cur.fetchall())
    except pymysql.MySQLError as exc:
        _raise_query_error(exc)
        return []


def _fetch_contacts(credit_codes: list[str]) -> dict[str, list[dict]]:
    """批量取这些企业的关联号码，按 credit_code 分组。"""
    codes = [code for code in dict.fromkeys(credit_codes) if code]
    if not codes:
        return {}

    columns = ", ".join(f"`{col}`" for col in CONTACT_COLUMNS)
    placeholders = ", ".join(["%s"] * len(codes))
    sql = (
        f"SELECT {columns} "
        "FROM `customer_contact` "
        f"WHERE `credit_code` IN ({placeholders}) "
        "ORDER BY `is_primary` DESC, `id`"
    )

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, tuple(codes))
            rows = list(cur.fetchall())
    except pymysql.MySQLError as exc:
        _raise_query_error(exc)
        return {}

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("credit_code") or ""), []).append(row)
    return grouped


# ------------------------------------------------------------------ 对外查询


def search_customers(keyword: str, limit: int = 10) -> list[dict]:
    """按 企业名称 → 法人姓名 → 电话号码 的顺序查询客户档案。

    每个结果包含企业主表字段，并附带：
        contacts: 该企业关联的全部号码
        match_type: 命中方式（company_name / legal_person / phone）
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise ValueError("查询关键字不能为空")

    limit = max(1, min(int(limit or 10), 50))
    like = f"%{_escape_like(keyword)}%"

    # ① 企业名称模糊
    rows = _query_profiles("`company_name` LIKE %s", (like,), limit)
    match_type = "company_name"

    # ② 法人姓名模糊
    if not rows:
        rows = _query_profiles("`legal_person` LIKE %s", (like,), limit)
        match_type = "legal_person"

    # ③ 电话号码精确（从表反查）
    if not rows:
        phone = _normalize_phone(keyword)
        if phone:
            rows = _query_profiles_by_phone(phone, limit)
            match_type = "phone"

    if not rows:
        return []

    contacts = _fetch_contacts([str(row.get("credit_code") or "") for row in rows])
    for row in rows:
        code = str(row.get("credit_code") or "")
        row["contacts"] = contacts.get(code, [])
        row["match_type"] = match_type
    return rows


def get_company_name(credit_code: str) -> str | None:
    """按信用代码取企业名称，供通知卡片展示。"""
    code = (credit_code or "").strip()
    if not code:
        return None
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `company_name` FROM `customer_profile` WHERE `credit_code` = %s LIMIT 1",
                (code,),
            )
            row = cur.fetchone()
            return str(row["company_name"]) if row and row.get("company_name") else None
    except pymysql.MySQLError as exc:
        _raise_query_error(exc)
        return None
    finally:
        conn.close()


def refresh_call_aggregates(credit_code: str) -> None:
    """按 call_record 重算企业通话次数与最近通话时间，重复执行安全。"""
    code = (credit_code or "").strip()
    if not code:
        return
    sql = (
        "UPDATE `customer_profile` AS p "
        "SET p.`call_times` = ("
        "      SELECT COUNT(*) FROM `call_record` AS c WHERE c.`credit_code` = p.`credit_code`"
        "    ), "
        "    p.`last_call_at` = ("
        "      SELECT MAX(c.`call_time`) FROM `call_record` AS c WHERE c.`credit_code` = p.`credit_code`"
        "    ) "
        "WHERE p.`credit_code` = %s"
    )
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (code,))
        conn.commit()
    except pymysql.MySQLError as exc:
        conn.rollback()
        _raise_query_error(exc)
    finally:
        conn.close()
