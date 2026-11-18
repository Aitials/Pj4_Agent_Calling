"""把已存在的 call_record 表升级到「官方回调字段」版本（可重复执行）。

背景：sql/006_create_call_record.sql 已按一知官方文档补齐字段，
但 `CREATE TABLE IF NOT EXISTS` 不会改动已存在的表，因此老库需要本脚本做增量迁移。

用法：
    .venv\\Scripts\\python.exe sql\\013_alter_call_record_aicc.py

执行内容：
    1. 补齐缺失字段（intent_level_tag_id / hangup_by / chat_round / properties 等）
    2. 放宽 credit_code 约束、扩展 supplier_level、把 asr_transcript 改为 MEDIUMTEXT
    3. 补建 (provider_job_id, call_time) 索引

只增不删，不修改已有数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

TABLE = "call_record"

# 字段名 -> 列定义（不含 ADD COLUMN）
NEW_COLUMNS: dict[str, str] = {
    "customer_name": "VARCHAR(128) DEFAULT NULL COMMENT '一知回调里的客户名称'",
    "callback_data_type": (
        "VARCHAR(64) DEFAULT NULL COMMENT '回调 dataType，区分新旧版'"
    ),
    "intent_level_tag_id": (
        "BIGINT UNSIGNED DEFAULT NULL COMMENT '一知意向标签组 ID'"
    ),
    "intent_level_code": (
        "INT DEFAULT NULL COMMENT '一知意向标签 code，与标签组 ID 唯一确定标签'"
    ),
    "hangup_by": (
        "VARCHAR(24) DEFAULT NULL COMMENT "
        "'挂断方：REMOTE_HANGUP/INITIAL_HANGUP/CS_HANGUP/OTHER_HANGUP'"
    ),
    "chat_round": "INT UNSIGNED DEFAULT NULL COMMENT '通话轮次'",
    "ring_duration": "INT UNSIGNED DEFAULT NULL COMMENT '振铃时长，单位秒'",
    "analysis_basis": "VARCHAR(512) DEFAULT NULL COMMENT '一知意向判定依据'",
    "customer_concern": "VARCHAR(512) DEFAULT NULL COMMENT '客户关注点，逗号分隔'",
    "record_expire_at": "DATE DEFAULT NULL COMMENT '录音预计过期日期，用于提醒转存'",
    "properties": (
        "JSON DEFAULT NULL COMMENT "
        "'导入时回传的自定义变量，建议含 credit_code/local_job_no'"
    ),
    "dynamic_properties": "JSON DEFAULT NULL COMMENT '通话过程中收集的动态变量'",
}

# 需要调整定义的已有字段
MODIFY_COLUMNS: dict[str, str] = {
    "credit_code": (
        "VARCHAR(32) DEFAULT NULL COMMENT '所属企业信用代码；号码未匹配到企业时为空'"
    ),
    "supplier_level": (
        "VARCHAR(64) DEFAULT NULL COMMENT '一知意向标签名称快照，仅供展示'"
    ),
    "asr_transcript": (
        "MEDIUMTEXT DEFAULT NULL COMMENT '完整转写，格式 [机器人] xxx\\\\n[客户] yyy'"
    ),
    "call_status": (
        "VARCHAR(24) NOT NULL COMMENT "
        "'归一状态：ANSWERED/NO_ANSWER/INVALID/REJECTED/FAILED/UNKNOWN'"
    ),
}

NEW_INDEXES: dict[str, str] = {
    "idx_provider_job_time": "(`provider_job_id`, `call_time`)",
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

            modified = 0
            for name, ddl in MODIFY_COLUMNS.items():
                if name not in existing:
                    continue
                cur.execute(f"ALTER TABLE `{TABLE}` MODIFY COLUMN `{name}` {ddl}")
                print(f"  ~ 调整字段 {name}")
                modified += 1

            indexed = 0
            for name, columns in NEW_INDEXES.items():
                cur.execute(
                    "SHOW INDEX FROM `call_record` WHERE Key_name = %s",
                    (name,),
                )
                if cur.fetchone():
                    continue
                cur.execute(f"ALTER TABLE `{TABLE}` ADD INDEX `{name}` {columns}")
                print(f"  + 新增索引 {name}")
                indexed += 1

        conn.commit()
        print(
            f"\n=== 完成：{database}.{TABLE} 新增 {added} 个字段、"
            f"调整 {modified} 个字段、新增 {indexed} 个索引 ==="
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
