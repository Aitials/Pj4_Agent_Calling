-- ============================================================
-- 客户档案业务表建表脚本
-- 数据库：MySQL 8.0+，字符集 utf8mb4
-- 说明：业务库已存在，本脚本只建表；如需一并建库，取消下方注释
-- ============================================================

-- CREATE DATABASE IF NOT EXISTS `ai_calling`
--   DEFAULT CHARACTER SET utf8mb4
--   DEFAULT COLLATE utf8mb4_0900_ai_ci;
USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `customer_profile` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `phone`            VARCHAR(32)     NOT NULL                COMMENT '手机号，业务唯一键（原始值）',
  `name`             VARCHAR(64)     DEFAULT NULL            COMMENT '客户姓名',
  `company_name`     VARCHAR(128)    DEFAULT NULL            COMMENT '公司名称（接口字段 compay_name）',
  `industry`         VARCHAR(64)     DEFAULT NULL            COMMENT '客户行业（接口不返回，人工补录或 Agent 提取）',
  `sex`              TINYINT         NOT NULL DEFAULT 0      COMMENT '性别：0未知 1男 2女',
  `note`             VARCHAR(512)    DEFAULT NULL            COMMENT '客户备注',

  `source_id`        BIGINT UNSIGNED DEFAULT NULL            COMMENT '好客搜客户记录 ID（接口 id）',
  `source_member_id` BIGINT UNSIGNED DEFAULT NULL            COMMENT '商户 ID（接口 member_id）',

  `crm_cate`         TINYINT         NOT NULL DEFAULT 0      COMMENT '客户意愿分类：0未分类 1意向客户 2沟通中 3试用中 4已成交 5无意向 6再跟踪 7未接',
  `phone_level`      TINYINT         NOT NULL DEFAULT 0      COMMENT 'AI 意向等级：0未评级 1=A 2=B 3=C 4=D 5=E 6=F',
  `phone_status`     TINYINT         NOT NULL DEFAULT 0      COMMENT '号码状态（好客搜原始值）',
  `call_times`       INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '累计拨打次数',
  `count_record`     INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '通话记录条数',
  `last_call_record_id` BIGINT UNSIGNED DEFAULT NULL         COMMENT '最近通话记录 ID（接口 call_record_id）',

  `task_id`          BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属外呼任务 ID，0 视为 NULL',
  `task_name`        VARCHAR(128)    DEFAULT NULL            COMMENT '所属任务名称',
  `seat_id`          BIGINT UNSIGNED DEFAULT NULL            COMMENT '所属坐席 ID，0 视为 NULL',
  `seat_name`        VARCHAR(64)     DEFAULT NULL            COMMENT '所属坐席名称',

  `source_created_at` DATETIME       DEFAULT NULL            COMMENT '好客搜创建时间',
  `source_updated_at` DATETIME       DEFAULT NULL            COMMENT '好客搜更新时间',
  `last_synced_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次同步时间',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '本地更新时间',

  `raw_json`         JSON            DEFAULT NULL            COMMENT '接口原始单条报文',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_phone` (`phone`),
  KEY `idx_crm_cate` (`crm_cate`),
  KEY `idx_phone_level` (`phone_level`),
  KEY `idx_task_id` (`task_id`),
  KEY `idx_source_created_at` (`source_created_at`),
  KEY `idx_last_synced_at` (`last_synced_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='客户个人档案主表';
