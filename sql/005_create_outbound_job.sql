-- ============================================================
-- 外呼任务表
-- 保存我方任务与一知 AICC 任务 ID 的映射及状态
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `outbound_job` (
  `id`              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `local_job_no`    VARCHAR(32)     NOT NULL                COMMENT '我方任务编号，唯一',
  `job_name`        VARCHAR(128)    NOT NULL                COMMENT '任务名称',
  `provider`        VARCHAR(32)     NOT NULL DEFAULT 'YIZHI' COMMENT '外呼供应商，本期固定 YIZHI',
  `provider_job_id` VARCHAR(64)     DEFAULT NULL            COMMENT '一知任务 ID，创建成功后回填',
  `line_id`         VARCHAR(64)     DEFAULT NULL            COMMENT '一知线路 ID（tenantPhoneNumberId）',
  `line_number`     VARCHAR(64)     DEFAULT NULL            COMMENT '线路显示号码，冗余展示',
  `concurrency`     INT UNSIGNED    NOT NULL DEFAULT 1      COMMENT '线路并发数，本期默认 1',
  `robot_count`     INT UNSIGNED    NOT NULL DEFAULT 1      COMMENT 'BOT 数量',
  `dialog_flow_id`  VARCHAR(64)     DEFAULT NULL            COMMENT '一知 BOT/话术 ID',
  `batch_no`        VARCHAR(32)     DEFAULT NULL            COMMENT '关联名单导入批次',
  `target_count`    INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '计划外呼号码数',
  `status`          VARCHAR(24)     NOT NULL DEFAULT 'CREATED' COMMENT 'CREATED/RUNNING/PAUSED/COMPLETED/FAILED',
  `status_raw`      VARCHAR(64)     DEFAULT NULL            COMMENT '一知原始状态值',
  `plan_start_at`   DATETIME        DEFAULT NULL            COMMENT '计划开始时间',
  `plan_end_at`     DATETIME        DEFAULT NULL            COMMENT '计划结束时间',
  `started_at`      DATETIME        DEFAULT NULL            COMMENT '实际启动时间',
  `finished_at`     DATETIME        DEFAULT NULL            COMMENT '实际结束时间',
  `created_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `raw_json`        JSON            DEFAULT NULL            COMMENT '一知任务创建/查询接口原始响应',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_local_job_no` (`local_job_no`),
  UNIQUE KEY `uk_provider_job` (`provider`, `provider_job_id`),
  KEY `idx_status` (`status`),
  KEY `idx_batch` (`batch_no`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='外呼任务表';
