-- ============================================================
-- 销售通知记录表
-- 按「企业 + 销售」去重，避免同一企业重复通知同一销售
-- ============================================================

USE `pj5_agent_calling`;

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
  `trigger_source`    VARCHAR(16)     NOT NULL DEFAULT 'AI'   COMMENT '触发来源：SUPPLIER一知标签 / AI我方判定',
  `status`            VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SENT/FAILED',
  `error_msg`         VARCHAR(512)    DEFAULT NULL            COMMENT '发送失败原因',
  `sent_at`           DATETIME        DEFAULT NULL            COMMENT '发送成功时间',
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_credit_assignee` (`credit_code`, `assignee_open_id`),
  KEY `idx_assignee` (`assignee_open_id`),
  KEY `idx_status` (`status`),
  KEY `idx_sent_at` (`sent_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售通知记录表';
