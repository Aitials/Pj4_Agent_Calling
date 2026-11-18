-- ============================================================
-- 销售通知记录表
-- 按「通话记录 + 销售」去重，同一通电话对同一销售只通知一次
-- 同一企业后续新的高意向通话会再次通知
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `dispatch_notice` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `credit_code`       VARCHAR(32)     NOT NULL                COMMENT '企业信用代码',
  `company_name`      VARCHAR(200)    DEFAULT NULL            COMMENT '企业名称，冗余用于卡片展示',
  `trigger_record_id` VARCHAR(64)     DEFAULT NULL            COMMENT '触发本次通知的通话记录 ID',
  `trigger_phone`     VARCHAR(32)     DEFAULT NULL            COMMENT '触发通知的号码',
  `assignee_open_id`  VARCHAR(64)     NOT NULL                COMMENT '被指派销售的飞书 open_id',
  `assignee_name`     VARCHAR(64)     DEFAULT NULL            COMMENT '销售姓名，冗余展示',
  `feishu_task_guid`  VARCHAR(64)     DEFAULT NULL            COMMENT '飞书任务 GUID',
  `card_message_id`   VARCHAR(64)     DEFAULT NULL            COMMENT '飞书卡片消息 ID',
  `trigger_source`    VARCHAR(16)     NOT NULL DEFAULT 'SUPPLIER' COMMENT '触发来源：SUPPLIER一知标签 / AI我方判定 / MANUAL人工',
  `status`            VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SENDING/SENT/FAILED',
  `error_msg`         VARCHAR(512)    DEFAULT NULL            COMMENT '发送失败原因',
  `retry_count`       TINYINT UNSIGNED NOT NULL DEFAULT 0      COMMENT '已重试次数',
  `next_retry_at`     DATETIME        DEFAULT NULL            COMMENT '下次重试时间',
  `notice_date`       DATE            DEFAULT NULL            COMMENT '通知日期，便于按天统计',
  `sent_at`           DATETIME        DEFAULT NULL            COMMENT '发送成功时间',
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_assignee` (`trigger_record_id`, `assignee_open_id`),
  KEY `idx_assignee` (`assignee_open_id`),
  KEY `idx_status` (`status`),
  KEY `idx_status_retry` (`status`, `next_retry_at`),
  KEY `idx_sent_at` (`sent_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售通知记录表';
