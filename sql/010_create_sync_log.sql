-- ============================================================
-- 数据同步日志表
-- 记录一知任务状态、通话记录等同步过程与增量游标
-- 游标只在 SUCCESS / PARTIAL 时推进
-- ============================================================

USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `sync_log` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `provider`         VARCHAR(32)     NOT NULL DEFAULT 'YIZHI' COMMENT '数据供应商，本期固定 YIZHI',
  `interface_name`   VARCHAR(64)     NOT NULL                COMMENT '同步对象：call_record/job_status/call_detail',
  `sync_start_time`  DATETIME        NOT NULL                COMMENT '本次同步开始时间',
  `sync_end_time`    DATETIME        DEFAULT NULL            COMMENT '本次同步结束时间',
  `records_fetched`  INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '接口拉取记录数',
  `records_saved`    INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '实际写入/更新记录数',
  `status`           VARCHAR(16)     NOT NULL DEFAULT 'RUNNING' COMMENT 'SUCCESS/PARTIAL/FAILED',
  `error_msg`        VARCHAR(512)    DEFAULT NULL            COMMENT '错误原因',
  `cursor_time`      DATETIME        DEFAULT NULL            COMMENT '本批最大业务时间，下次增量同步起点',

  PRIMARY KEY (`id`),
  KEY `idx_interface_time` (`provider`, `interface_name`, `sync_start_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='数据同步日志表';
