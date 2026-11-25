"""v3.2：dispatch_notice 增加 sending_at（抢占时间），供超时回收使用（可重复执行）。

字段用途：
    sending_at  记录被抢占为 SENDING 的时间；超时回收必须依据它，
                不能再用 created_at（入队时间），否则会误回收正在发送的记录。

用法：
    .venv\\Scripts\\python.exe sql\\031_alter_dispatch_notice_sending_at.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

TABLE = "dispatch_notice"

NEW_COLUMNS: dict[str, str] = {
    "sending_at": "DATETIME DEFAULT NULL COMMENT '被抢占为 SENDING 的时间'",
}

NEW_INDEXES: dict[str, str] = {
    "idx_status_sending": "(`status`, `sending_at`)",
}


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
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name = %s",
                (database, TABLE),
            )
            if not cur.fetchone()[0]:
                print(f"[跳过] {database}.{TABLE} 不存在，请先执行 sql/025_rebuild_dispatch_notice_v3.py")
                return 0

            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (database, TABLE),
            )
            existing = {row[0] for row in cur.fetchall()}

            added = 0
            for name, ddl in NEW_COLUMNS.items():
                if name in existing:
                    continue
                cur.execute(f"ALTER TABLE `{TABLE}` ADD COLUMN `{name}` {ddl}")
                print(f"  + 新增字段 {name}")
                added += 1

            indexed = 0
            for name, columns in NEW_INDEXES.items():
                cur.execute(f"SHOW INDEX FROM `{TABLE}` WHERE Key_name = %s", (name,))
                if cur.fetchone():
                    continue
                cur.execute(f"ALTER TABLE `{TABLE}` ADD INDEX `{name}` {columns}")
                print(f"  + 新增索引 {name}")
                indexed += 1

        conn.commit()
        print(f"\n=== 完成：{database}.{TABLE} 新增 {added} 个字段、{indexed} 个索引 ===")
        return 0
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
