"""v3.4：钉钉成员通讯录本地缓存表（可重复执行）。

用途：
    把钉钉通讯录同步到本地，避免每次找人/查 unionId 都实时打钉钉接口。
    与 feishu_user 解耦：钉钉成员只落这张表，不混进飞书通讯录。

字段与 feishu_user 对齐，额外保留 union_id（创建待办需要）。

用法：
    .venv\\Scripts\\python.exe sql\\033_create_dingtalk_user.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

TABLE = "dingtalk_user"

DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
  `user_id`          VARCHAR(64)  NOT NULL                COMMENT '钉钉明文 userId，主键',
  `union_id`         VARCHAR(64)  DEFAULT NULL            COMMENT '钉钉 unionId，创建待办用',
  `name`             VARCHAR(64)  DEFAULT NULL            COMMENT '姓名',
  `mobile`           VARCHAR(32)  DEFAULT NULL            COMMENT '手机号（权限允许时同步）',
  `title`            VARCHAR(128) DEFAULT NULL            COMMENT '职位',
  `department_ids`   JSON         DEFAULT NULL            COMMENT '所属部门 ID 列表',
  `department_names` VARCHAR(255) DEFAULT NULL            COMMENT '部门名称，冗余便于展示',
  `status`           TINYINT      NOT NULL DEFAULT 0      COMMENT '钉钉用户状态原始值',
  `is_deleted`       TINYINT      NOT NULL DEFAULT 0      COMMENT '软删除：0正常 1已离职或不在授权范围',
  `synced_at`        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次同步时间',
  `created_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `updated_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP COMMENT '本地更新时间',
  `raw_json`         JSON         DEFAULT NULL            COMMENT '钉钉接口原始单条报文',

  PRIMARY KEY (`user_id`),
  KEY `idx_union_id` (`union_id`),
  KEY `idx_name` (`name`),
  KEY `idx_is_deleted` (`is_deleted`),
  KEY `idx_synced_at` (`synced_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='钉钉成员通讯录本地缓存'
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
            existed = bool(cur.fetchone()[0])
            cur.execute(DDL)
        conn.commit()
        action = "已存在，跳过" if existed else "已创建"
        print(f"=== 完成：{database}.{TABLE} {action} ===")
        return 0
    except pymysql.MySQLError as exc:
        conn.rollback()
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
