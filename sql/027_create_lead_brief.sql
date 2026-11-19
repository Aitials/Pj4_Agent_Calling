-- ============================================================
-- AI 客户分析任务表（v3）
-- 一条通话记录一条任务，由 run_analyzer 抢占执行
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `lead_brief` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `record_id`     VARCHAR(64)     NOT NULL                COMMENT '关联 call_record.record_id，唯一',
  `credit_code`   VARCHAR(32)     DEFAULT NULL            COMMENT '企业信用代码',
  `intent_code`   INT             DEFAULT NULL            COMMENT '触发时的意向 code 快照',
  `analysis_id`   BIGINT UNSIGNED DEFAULT NULL            COMMENT '当前有效分析结果 agent_analysis.id',
  `status`        VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/PROCESSING/RETRY/DONE/FAILED/TIMEOUT',
  `retry_count`   INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '已重试次数',
  `next_retry_at` DATETIME        DEFAULT NULL            COMMENT '下次可执行时间',
  `locked_at`     DATETIME        DEFAULT NULL            COMMENT '任务锁定时间',
  `worker_id`     VARCHAR(100)    DEFAULT NULL            COMMENT '当前执行的 Worker 标识',
  `last_error`    TEXT            DEFAULT NULL            COMMENT '最近错误信息',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_id` (`record_id`),
  KEY `idx_status_retry` (`status`, `next_retry_at`),
  KEY `idx_credit_code` (`credit_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='AI 客户分析任务表';
