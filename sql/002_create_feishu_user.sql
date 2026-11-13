-- ============================================================
-- 飞书成员通讯录本地缓存表
-- 用途：把飞书通讯录同步到本地，避免每次找人/发消息都实时打飞书接口
-- 说明：部门字段为预留列，当前通讯录权限范围未覆盖部门，先留空
-- ============================================================

USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `feishu_user` (
  `open_id`         VARCHAR(64)  NOT NULL                COMMENT '用户在本应用内的 open_id，主键',
  `union_id`        VARCHAR(64)  DEFAULT NULL            COMMENT '跨应用用户标识',
  `name`            VARCHAR(64)  DEFAULT NULL            COMMENT '姓名',
  `en_name`         VARCHAR(64)  DEFAULT NULL            COMMENT '英文名',
  `mobile_visible`  TINYINT      NOT NULL DEFAULT 0      COMMENT '手机号是否可见：0否 1是',

  `department_ids`  JSON         DEFAULT NULL            COMMENT '【预留】所属部门 ID 列表',
  `department_names` VARCHAR(255) DEFAULT NULL           COMMENT '【预留】部门名称，冗余便于展示',

  `status`          TINYINT      NOT NULL DEFAULT 0      COMMENT '飞书用户状态原始值',
  `is_agent`        TINYINT      NOT NULL DEFAULT 0      COMMENT '是否机器人：0否 1是',
  `is_deleted`      TINYINT      NOT NULL DEFAULT 0      COMMENT '软删除：0正常 1已离职或不在可见范围',

  `synced_at`       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次同步时间',
  `created_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `updated_at`      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '本地更新时间',

  `raw_json`        JSON         DEFAULT NULL            COMMENT '飞书接口原始单条报文',

  PRIMARY KEY (`open_id`),
  KEY `idx_name` (`name`),
  KEY `idx_union_id` (`union_id`),
  KEY `idx_is_deleted` (`is_deleted`),
  KEY `idx_synced_at` (`synced_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='飞书成员通讯录本地缓存';
