-- ============================================================
-- AI 分析结果表（v3）
-- 一条通话记录 + 版本号确定一条结果；新版本追加，不覆盖已发送结果
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `agent_analysis` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `lead_brief_id`     BIGINT UNSIGNED DEFAULT NULL            COMMENT '关联 lead_brief.id',
  `version`          INT UNSIGNED    NOT NULL DEFAULT 1       COMMENT '同一条通话的分析版本号',
  `record_id`        VARCHAR(64)     NOT NULL                COMMENT '关联 call_record.record_id',
  `credit_code`      VARCHAR(32)     NOT NULL                COMMENT '所属企业信用代码',
  `customer_phone`   VARCHAR(32)     NOT NULL                COMMENT '被叫号码',
  `intent_level`     VARCHAR(16)     NOT NULL                COMMENT '意向等级：HIGH/MEDIUM/LOW/INVALID',
  `reason`           VARCHAR(512)    DEFAULT NULL            COMMENT '意向等级判定理由',
  `quote`            VARCHAR(512)    DEFAULT NULL            COMMENT '支撑判断的通话原文引用',
  `demand`           VARCHAR(512)    DEFAULT NULL            COMMENT '客户需求（摘要）',
  `budget`           VARCHAR(128)    DEFAULT NULL            COMMENT '预算信息',
  `objection`        VARCHAR(512)    DEFAULT NULL            COMMENT '客户异议（摘要）',
  `blocked`          TINYINT         NOT NULL DEFAULT 0      COMMENT '是否应阻断后续触达：0否 1是',
  `block_reason`     VARCHAR(64)     DEFAULT NULL            COMMENT '阻断原因：空号/明确拒绝/勿扰等',
  `suggestion`       VARCHAR(512)    DEFAULT NULL            COMMENT '销售跟进建议（摘要）',
  `best_follow_at`   VARCHAR(64)     DEFAULT NULL            COMMENT '建议联系时间，保留模型输出原文',
  `supplier_level`   VARCHAR(16)     DEFAULT NULL            COMMENT '一知意向标签快照，用于对比',
  `level_consistent` TINYINT         DEFAULT NULL            COMMENT '与一知标签是否一致：0否 1是',
  `customer_needs`   JSON            DEFAULT NULL            COMMENT '客户需求列表',
  `concerns`         JSON            DEFAULT NULL            COMMENT '客户关注点列表',
  `objections`       JSON            DEFAULT NULL            COMMENT '客户异议列表',
  `intent_evidence`  JSON            DEFAULT NULL            COMMENT '意向判断依据列表',
  `opening`          TEXT            DEFAULT NULL            COMMENT '销售开场话术',
  `value_points`     JSON            DEFAULT NULL            COMMENT '产品价值切入点列表',
  `objection_handling` JSON          DEFAULT NULL            COMMENT '异议处理话术列表',
  `next_action`      TEXT            DEFAULT NULL            COMMENT '下一步跟进建议',
  `model`            VARCHAR(64)     DEFAULT NULL            COMMENT '使用的模型名称',
  `analyzed_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分析时间',
  `raw_json`         JSON            DEFAULT NULL            COMMENT '模型结构化原始输出',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_version` (`record_id`, `version`),
  KEY `idx_lead_brief_id` (`lead_brief_id`),
  KEY `idx_intent` (`intent_level`),
  KEY `idx_credit` (`credit_code`),
  KEY `idx_analyzed_at` (`analyzed_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI 分析结果表';
