"""v3：重建 dispatch_notice 表（一通电话一条通知）。

决策：不备份旧数据，直接 DROP 后按新结构重建。

变化：
    1. 唯一键由 (trigger_record_id, assignee_open_id) 改为 (trigger_record_id)
    2. trigger_record_id 收紧为 NOT NULL
    3. 新增 analysis_status / analysis_retry_count，支撑分析结果通知
    4. 新增 updated_at，其余字段保持原样（assignee_open_id / card_message_id / error_msg 沿用）

用法：
    .venv\\Scripts\\python.exe sql\\025_rebuild_dispatch_notice_v3.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

TABLE = "dispatch_notice"

DROP_SQL = f"DROP TABLE IF EXISTS `{TABLE}`"

CREATE_SQL = """
CREATE TABLE `dispatch_notice` (
  `id`                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `credit_code`           VARCHAR(32)     NOT NULL                COMMENT '企业信用代码',
  `company_name`          VARCHAR(200)    DEFAULT NULL            COMMENT '企业名称，冗余用于卡片展示',
  `trigger_record_id`     VARCHAR(64)     NOT NULL                COMMENT '触发本次通知的通话记录 ID，唯一',
  `trigger_phone`         VARCHAR(32)     DEFAULT NULL            COMMENT '触发通知的号码',
  `assignee_open_id`      VARCHAR(64)     NOT NULL                COMMENT '被指派销售的飞书 open_id',
  `assignee_name`         VARCHAR(64)     DEFAULT NULL            COMMENT '销售姓名，冗余展示',
  `feishu_task_guid`      VARCHAR(64)     DEFAULT NULL            COMMENT '飞书任务 GUID',
  `card_message_id`       VARCHAR(64)     DEFAULT NULL            COMMENT '飞书卡片消息 ID',
  `trigger_source`        VARCHAR(16)     NOT NULL DEFAULT 'SUPPLIER' COMMENT '触发来源：SUPPLIER一知标签 / AI我方判定 / MANUAL人工',
  `status`                VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SENDING/SENT/FAILED',
  `error_msg`             VARCHAR(512)    DEFAULT NULL            COMMENT '最近错误信息',
  `retry_count`           TINYINT UNSIGNED NOT NULL DEFAULT 0     COMMENT '基础通知已重试次数',
  `next_retry_at`         DATETIME        DEFAULT NULL            COMMENT '基础通知下次重试时间',
  `analysis_status`       VARCHAR(16)     DEFAULT NULL            COMMENT '分析通知状态：PENDING/SENDING/SENT/FAILED',
  `analysis_retry_count`  TINYINT UNSIGNED NOT NULL DEFAULT 0     COMMENT '分析通知已重试次数',
  `notice_date`           DATE            DEFAULT NULL            COMMENT '通知日期，便于按天统计',
  `sent_at`               DATETIME        DEFAULT NULL            COMMENT '基础通知发送成功时间',
  `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record` (`trigger_record_id`),
  KEY `idx_assignee` (`assignee_open_id`),
  KEY `idx_status` (`status`),
  KEY `idx_status_retry` (`status`, `next_retry_at`),
  KEY `idx_analysis_status` (`analysis_status`),
  KEY `idx_sent_at` (`sent_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售通知记录表'
"""


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
            exists = bool(cur.fetchone()[0])
            if exists:
                cur.execute("SELECT COUNT(*) FROM `dispatch_notice`")
                print(f"  旧表记录 {cur.fetchone()[0]} 条，将直接删除（不备份）")

            cur.execute(DROP_SQL)
            print(f"  - 已删除旧表 {TABLE}")
            cur.execute(CREATE_SQL)
            print(f"  + 已按新结构重建 {TABLE}（唯一键 trigger_record_id）")

        conn.commit()
        print(f"\n=== 完成：{database}.{TABLE} 已重建 ===")
        return 0
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
