"""扫描本机 MySQL：列出业务库与表结构（只读）。

连接信息从 .env 读取：
    MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD

用法：
    .venv\\Scripts\\python.exe scripts\\db_scan.py
    .venv\\Scripts\\python.exe scripts\\db_scan.py --database your_db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}


def build_config(database: str | None = None) -> dict:
    password = get_env("MYSQL_PASSWORD")
    if not password:
        raise SystemExit(
            "缺少 MYSQL_PASSWORD。请在 .env 中配置：\n"
            "  MYSQL_HOST=127.0.0.1\n"
            "  MYSQL_PORT=3306\n"
            "  MYSQL_USER=root\n"
            "  MYSQL_PASSWORD=***\n"
            "  MYSQL_DATABASE=你的库名"
        )
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip(),
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "connect_timeout": 8,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描 MySQL 库表结构（只读）")
    parser.add_argument("--database", default=get_env("MYSQL_DATABASE").strip(), help="只看指定库")
    args = parser.parse_args()

    try:
        conn = pymysql.connect(**build_config())
    except pymysql.MySQLError as exc:
        print(f"[连接失败] {exc}")
        return 1

    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            print(f"MySQL 版本：{version}\n")

            if args.database:
                databases = [args.database]
            else:
                cur.execute("SHOW DATABASES")
                databases = [
                    row[0] for row in cur.fetchall() if row[0] not in SYSTEM_DATABASES
                ]

            print(f"业务库（{len(databases)} 个）：")
            for db in databases:
                print(f"\n=== {db} ===")
                cur.execute(
                    "SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                    (db,),
                )
                tables = cur.fetchall()
                if not tables:
                    print("  （空库，无表）")
                    continue
                for name, rows, comment in tables:
                    print(f"  - {name}  约 {rows or 0} 行  {comment or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
