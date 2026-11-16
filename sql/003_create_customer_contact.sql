-- ============================================================
-- 企业联系人/号码从表
-- 一个企业可有多个号码；号码全局唯一，避免同一号码挂到两家企业
-- ============================================================

USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `customer_contact` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `credit_code`      VARCHAR(32)     NOT NULL                COMMENT '所属企业统一社会信用代码',
  `phone`            VARCHAR(32)     NOT NULL                COMMENT '规范化电话号码，唯一',
  `phone_type`       VARCHAR(16)     NOT NULL DEFAULT 'MOBILE' COMMENT '号码类型：MOBILE手机 / LANDLINE固话',
  `contact_name`     VARCHAR(64)     DEFAULT NULL            COMMENT '联系人姓名，企查查数据可能为空',
  `is_primary`       TINYINT         NOT NULL DEFAULT 0      COMMENT '是否主号码：0否 1是',
  `is_blacklist`     TINYINT         NOT NULL DEFAULT 0      COMMENT '号码级黑名单：0否 1是',
  `block_reason`     VARCHAR(64)     DEFAULT NULL            COMMENT '阻断原因：EMPTY_NUMBER/REJECTED/NOT_OWNER/DO_NOT_DISTURB',
  `call_times`       INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '该号码累计拨打次数',
  `last_call_at`     DATETIME        DEFAULT NULL            COMMENT '最近一次拨打时间',
  `last_call_status` VARCHAR(24)     DEFAULT NULL            COMMENT '最近一次通话结果',
  `ai_intent_level`  VARCHAR(16)     DEFAULT NULL            COMMENT '该号码最近一次 AI 意向',
  `source_channel`   VARCHAR(32)     NOT NULL DEFAULT 'QICHACHA' COMMENT '数据来源',
  `import_batch_no`  VARCHAR(32)     DEFAULT NULL            COMMENT '导入批次号',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_phone` (`phone`),
  KEY `idx_credit_code` (`credit_code`),
  KEY `idx_blacklist` (`is_blacklist`),
  KEY `idx_intent` (`ai_intent_level`),
  KEY `idx_last_call` (`last_call_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='企业联系人/号码从表';
