"""v3：把 agent_analysis 改造成「客户分析结果表」（可重复执行）。

决策：不新建 customer_analysis，原地改造现有 agent_analysis。

变化：
    1. 新增 lead_brief_id / version，唯一键改为 (record_id, version)，支持历史版本
    2. 新增结构化话术字段：customer_needs / concerns / objections / intent_evidence /
       opening / value_points / objection_handling / next_action
    3. 旧扁平字段全部保留，写入时与结构化字段双写，作为摘要兼容

用法：
    .venv\\Scripts\\python.exe sql\\026_alter_agent_analysis_v3.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

TABLE = "agent_analysis"

NEW_COLUMNS: dict[str, str] = {
    "lead_brief_id": "BIGINT UNSIGNED DEFAULT NULL COMMENT '关联 lead_brief.id'",
    "version": "INT UNSIGNED NOT NULL DEFAULT 1 COMMENT '同一条通话的分析版本号'",
    "customer_needs": "JSON DEFAULT NULL COMMENT '客户需求列表'",
    "concerns": "JSON DEFAULT NULL COMMENT '客户关注点列表'",
    "objections": "JSON DEFAULT NULL COMMENT '客户异议列表'",
    "intent_evidence": "JSON DEFAULT NULL COMMENT '意向判断依据列表'",
    "opening": "TEXT DEFAULT NULL COMMENT '销售开场话术'",
    "value_points": "JSON DEFAULT NULL COMMENT '产品价值切入点列表'",
    "objection_handling": "JSON DEFAULT NULL COMMENT '异议处理话术列表'",
    "next_action": "TEXT DEFAULT NULL COMMENT '下一步跟进建议'",
}

OLD_UNIQUE = "uk_record_id"
NEW_UNIQUE = "uk_record_version"
NEW_INDEXES: dict[str, str] = {
    "idx_lead_brief_id": "(`lead_brief_id`)",
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
                print(f"[跳过] {database}.{TABLE} 不存在，请先执行 sql/000_init_db.py")
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

            cur.execute(f"SHOW INDEX FROM `{TABLE}` WHERE Key_name = %s", (OLD_UNIQUE,))
            if cur.fetchone():
                cur.execute(f"ALTER TABLE `{TABLE}` DROP INDEX `{OLD_UNIQUE}`")
                print(f"  - 删除旧唯一键 {OLD_UNIQUE}")

            cur.execute(f"SHOW INDEX FROM `{TABLE}` WHERE Key_name = %s", (NEW_UNIQUE,))
            if not cur.fetchone():
                cur.execute(
                    f"ALTER TABLE `{TABLE}` "
                    f"ADD UNIQUE KEY `{NEW_UNIQUE}` (`record_id`, `version`)"
                )
                print(f"  + 新增唯一键 {NEW_UNIQUE} (record_id, version)")

            indexed = 0
            for name, columns in NEW_INDEXES.items():
                cur.execute(f"SHOW INDEX FROM `{TABLE}` WHERE Key_name = %s", (name,))
                if cur.fetchone():
                    continue
                cur.execute(f"ALTER TABLE `{TABLE}` ADD INDEX `{name}` {columns}")
                print(f"  + 新增索引 {name}")
                indexed += 1

        conn.commit()
        print(
            f"\n=== 完成：{database}.{TABLE} 新增 {added} 个字段、"
            f"新增 {indexed} 个索引、唯一键改为 (record_id, version) ==="
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
