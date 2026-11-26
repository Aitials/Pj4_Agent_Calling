"""v3.3：派单表增加渠道维度，支持钉钉派单与待办（可重复执行）。

字段用途：
    sales_assignee.dingtalk_userid     钉钉明文 userId，仅派单轮转使用
    dispatch_notice.channel            该条通知归属的渠道：feishu / dingtalk
    dispatch_notice.todo_id            钉钉待办 ID，建完立即回写，供重试跳过创建
    dispatch_assignee_cursor.channel   轮转游标按渠道独立，主键由 id 改为 channel

存量数据处理：
    dispatch_notice 存量行统一落到 'feishu'；
    dispatch_assignee_cursor 存量单行升级为 feishu 游标。

用法：
    .venv\\Scripts\\python.exe sql\\032_alter_dispatch_channel.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

SALES_TABLE = "sales_assignee"
NOTICE_TABLE = "dispatch_notice"
CURSOR_TABLE = "dispatch_assignee_cursor"

SALES_COLUMNS: dict[str, str] = {
    "dingtalk_userid": (
        "VARCHAR(64) DEFAULT NULL COMMENT '钉钉明文 userId，仅派单用'"
    ),
}

NOTICE_COLUMNS: dict[str, str] = {
    "channel": (
        "VARCHAR(16) NOT NULL DEFAULT 'feishu' COMMENT '派单渠道：feishu / dingtalk'"
    ),
    "todo_id": "VARCHAR(64) DEFAULT NULL COMMENT '钉钉待办 ID'",
}

NEW_INDEXES: list[tuple[str, str, str]] = [
    (SALES_TABLE, "idx_dingtalk_userid", "(`dingtalk_userid`)"),
    (NOTICE_TABLE, "idx_status_channel", "(`status`, `channel`)"),
]


def build_config(database: str) -> dict:
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip() or "root",
        "password": get_env("MYSQL_PASSWORD"),
        "database": database,
        "charset": "utf8mb4",
        "connect_timeout": 8,
    }


def _table_exists(cur, database: str, table: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s",
        (database, table),
    )
    return bool(cur.fetchone()[0])


def _columns(cur, database: str, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (database, table),
    )
    return {row[0] for row in cur.fetchall()}


def _primary_key(cur, database: str, table: str) -> list[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.key_column_usage "
        "WHERE table_schema = %s AND table_name = %s "
        "  AND constraint_name = 'PRIMARY' "
        "ORDER BY ordinal_position",
        (database, table),
    )
    return [row[0] for row in cur.fetchall()]


def _add_columns(cur, database: str, table: str, columns: dict[str, str]) -> int:
    existing = _columns(cur, database, table)
    added = 0
    for name, ddl in columns.items():
        if name in existing:
            continue
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {ddl}")
        print(f"  + {table}.{name}")
        added += 1
    return added


def _add_indexes(cur, database: str) -> int:
    indexed = 0
    for table, name, columns in NEW_INDEXES:
        cur.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (name,))
        if cur.fetchone():
            continue
        cur.execute(f"ALTER TABLE `{table}` ADD INDEX `{name}` {columns}")
        print(f"  + {table}.{name}")
        indexed += 1
    return indexed


def _migrate_cursor(cur, database: str) -> int:
    """游标表从「单行 id」升级为「按 channel 一行」。"""
    if not _table_exists(cur, database, CURSOR_TABLE):
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{CURSOR_TABLE}` (
              `channel`      VARCHAR(16) NOT NULL COMMENT '派单渠道：feishu / dingtalk',
              `last_open_id` VARCHAR(64) DEFAULT NULL COMMENT '上次分配到的通道用户 ID',
              `updated_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                          ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
              PRIMARY KEY (`channel`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
              COMMENT='按渠道独立的轮流分配游标'
            """
        )
        print(f"  + 新建表 {CURSOR_TABLE}")
        added = 0
    else:
        added = _add_columns(
            cur,
            database,
            CURSOR_TABLE,
            {
                "channel": (
                    "VARCHAR(16) NOT NULL DEFAULT 'feishu' "
                    "COMMENT '派单渠道：feishu / dingtalk'"
                )
            },
        )
        existing = _columns(cur, database, CURSOR_TABLE)
        primary = _primary_key(cur, database, CURSOR_TABLE)
        if "id" in existing or primary != ["channel"]:
            # 存量行统一视为 feishu；主键切到 channel 后一个渠道一行
            cur.execute(f"UPDATE `{CURSOR_TABLE}` SET `channel` = 'feishu'")
            clauses = ["DROP PRIMARY KEY"]
            if "id" in existing:
                clauses.append("DROP COLUMN `id`")
            clauses.append("ADD PRIMARY KEY (`channel`)")
            cur.execute(f"ALTER TABLE `{CURSOR_TABLE}` " + ", ".join(clauses))
            print(f"  + {CURSOR_TABLE} 主键改为 (channel)")

    cur.execute(
        f"INSERT IGNORE INTO `{CURSOR_TABLE}` (`channel`, `last_open_id`) "
        "VALUES ('feishu', NULL)"
    )
    return added


def main() -> int:
    database = get_env("MYSQL_DATABASE").strip() or "agent_calling"
    password = get_env("MYSQL_PASSWORD")
    if not password:
        print("缺少 MYSQL_PASSWORD，请在 .env 中配置后重试")
        return 1

    try:
        conn = pymysql.connect(**build_config(database))
    except pymysql.MySQLError as exc:
        print(f"[连接失败] {exc}")
        return 1

    try:
        with conn.cursor() as cur:
            for table in (SALES_TABLE, NOTICE_TABLE):
                if not _table_exists(cur, database, table):
                    print(f"[跳过] {database}.{table} 不存在，请先执行基础建表脚本")
                    return 0

            added = _add_columns(cur, database, SALES_TABLE, SALES_COLUMNS)
            added += _add_columns(cur, database, NOTICE_TABLE, NOTICE_COLUMNS)
            added += _migrate_cursor(cur, database)
            indexed = _add_indexes(cur, database)

        conn.commit()
        print(
            f"\n=== 完成：{database} 新增 {added} 个字段、{indexed} 个索引 ==="
        )
        return 0
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
