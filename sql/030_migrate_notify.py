"""高意向通知相关表结构迁移（可重复执行）。

执行内容：
    1. 创建 intent_level_rule / sales_assignee / dispatch_assignee_cursor
    2. 把 dispatch_notice 唯一键从 (credit_code, assignee) 改为 (record_id, assignee)
    3. 补 retry_count / next_retry_at / notice_date 与重试索引
    4. 写入测试销售（024_seed_sales_assignee.sql）

用法：
    .venv\\Scripts\\python.exe sql\\030_migrate_notify.py
"""

from __future__ import annotations

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

SQL_DIR = PROJECT_ROOT / "sql"
SETUP_FILES = (
    "020_create_intent_rule.sql",
    "021_create_sales_assignee.sql",
    "022_create_dispatch_cursor.sql",
    "024_seed_sales_assignee.sql",
)

NEW_COLUMNS = {
    "retry_count": "TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已重试次数'",
    "next_retry_at": "DATETIME DEFAULT NULL COMMENT '下次重试时间'",
    "notice_date": "DATE DEFAULT NULL COMMENT '通知日期，便于按天统计'",
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


def split_statements(sql: str) -> list[str]:
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [stmt.strip() for stmt in "\n".join(lines).split(";") if stmt.strip()]


def run_setup_files(cur) -> int:
    executed = 0
    for name in SETUP_FILES:
        path = SQL_DIR / name
        if not path.exists():
            print(f"  ! 缺少 {name}，跳过")
            continue
        for stmt in split_statements(path.read_text(encoding="utf-8")):
            if stmt.upper().startswith("USE"):
                continue
            cur.execute(stmt)
            executed += 1
        print(f"  ✓ {name}")
    return executed


def main() -> int:
    database = get_env("MYSQL_DATABASE").strip() or "agent_calling"
    if not get_env("MYSQL_PASSWORD"):
        print("缺少 MYSQL_PASSWORD，请在 .env 中配置")
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
                "WHERE table_schema = %s AND table_name = 'dispatch_notice'",
                (database,),
            )
            if not cur.fetchone()[0]:
                print("[错误] dispatch_notice 不存在，请先执行 sql/000_init_db.py")
                return 1

            print("=== 1. 创建新表与测试销售 ===")
            run_setup_files(cur)

            print("\n=== 2. 检查重复通知 ===")
            cur.execute(
                "SELECT trigger_record_id, assignee_open_id, COUNT(*) AS c "
                "FROM dispatch_notice "
                "WHERE trigger_record_id IS NOT NULL "
                "GROUP BY trigger_record_id, assignee_open_id HAVING c > 1"
            )
            duplicates = cur.fetchall()
            if duplicates:
                print(f"[中止] 存在 {len(duplicates)} 组重复通知，请先清理后再迁移")
                for row in duplicates:
                    print("  ", row)
                return 1
            print("  ✓ 无重复")

            print("\n=== 3. 调整 dispatch_notice ===")
            cur.execute("SHOW INDEX FROM dispatch_notice WHERE Key_name = %s", ("uk_credit_assignee",))
            if cur.fetchone():
                cur.execute("ALTER TABLE dispatch_notice DROP INDEX uk_credit_assignee")
                print("  ~ 删除旧唯一键 uk_credit_assignee")

            cur.execute("SHOW INDEX FROM dispatch_notice WHERE Key_name = %s", ("uk_record_assignee",))
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE dispatch_notice ADD UNIQUE KEY uk_record_assignee "
                    "(trigger_record_id, assignee_open_id)"
                )
                print("  + 新增唯一键 uk_record_assignee")

            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = 'dispatch_notice'",
                (database,),
            )
            existing = {row[0] for row in cur.fetchall()}
            for name, ddl in NEW_COLUMNS.items():
                if name in existing:
                    continue
                cur.execute(f"ALTER TABLE dispatch_notice ADD COLUMN `{name}` {ddl}")
                print(f"  + 新增字段 {name}")

            cur.execute("SHOW INDEX FROM dispatch_notice WHERE Key_name = %s", ("idx_status_retry",))
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE dispatch_notice ADD INDEX idx_status_retry (status, next_retry_at)"
                )
                print("  + 新增索引 idx_status_retry")

            cur.execute("SELECT open_id, name FROM sales_assignee WHERE enabled = 1")
            print("\n=== 4. 当前销售名单 ===")
            for row in cur.fetchall():
                print(f"  - {row[0]}  {row[1] or ''}")

        conn.commit()
        print("\n=== 迁移完成 ===")
        return 0
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
