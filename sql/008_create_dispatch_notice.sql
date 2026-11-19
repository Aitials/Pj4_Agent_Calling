-- ============================================================
-- 销售通知记录表（v3：一通电话一条记录）
-- 唯一键为 trigger_record_id；同一企业后续新的高意向通话会再次通知
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `dispatch_notice` (
  `id`                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `credit_code`           VARCHAR(32)     NOT NULL                COMMENT '企业信用代码',
  `company_name`          VARCHAR(200)    DEFAULT NULL            COMMENT '企业名称，冗余用于卡片展示',
  `trigger_record_id`     VARCHAR(64)     NOT NULL                COMMENT '触发本次通知的通话记录 ID，唯一',
  `trigger_phone`         VARCHAR(32)     DEFAULT NULL            COMMENT '触发通知的号码',
  `assignee_open_id`      VARCHAR(64)     NOT NULL                COMMENT '被指派销售的飞书 open_id',
  `assignee_name`         VARCHAR(64)     DEFAULT NULL            COMMENT '销售姓名，冗余展示',
  `feishu_task_guid`      VARCHAR(64)     DEFAULT NULL            COMMENT '飞书任务 GUID',
  `card_message_id`       VARCHAR(64)     DEFAULT NULL            COMMENT '飞书卡片消息 ID',
  `trigger_source`        VARCHAR(16)     NOT NULL DEFAULT 'SUPPLIER' COMMENT '触发来源：SUPPLIER一知标签 / AI我方判定 / MANUAL人工',
  `status`                VARCHAR(16)     NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING/SENDING/SENT/FAILED',
  `error_msg`             VARCHAR(512)    DEFAULT NULL            COMMENT '最近错误信息',
  `retry_count`           TINYINT UNSIGNED NOT NULL DEFAULT 0     COMMENT '基础通知已重试次数',
  `next_retry_at`         DATETIME        DEFAULT NULL            COMMENT '基础通知下次重试时间',
  `analysis_status`       VARCHAR(16)     DEFAULT NULL            COMMENT '分析通知状态：PENDING/SENDING/SENT/FAILED',
  `analysis_retry_count`  TINYINT UNSIGNED NOT NULL DEFAULT 0     COMMENT '分析通知已重试次数',
  `attachment_guid`       VARCHAR(64)     DEFAULT NULL            COMMENT '飞书任务附件 GUID',
  `attachment_url`        VARCHAR(1024)   DEFAULT NULL            COMMENT '附件访问地址',
  `attachment_local_path` VARCHAR(512)    DEFAULT NULL            COMMENT '本地简报存档路径',
  `attachment_status`     VARCHAR(16)     DEFAULT NULL            COMMENT 'NULL/PENDING/UPLOADING/DONE/FAILED/SKIPPED',
  `attachment_retry_count` TINYINT UNSIGNED NOT NULL DEFAULT 0    COMMENT '附件上传重试次数',
  `attachment_error`      VARCHAR(512)    DEFAULT NULL            COMMENT '附件处理失败原因',
  `attachment_updated_at` DATETIME        DEFAULT NULL            COMMENT '附件最后一次处理时间',
  `notice_date`           DATE            DEFAULT NULL            COMMENT '通知日期，便于按天统计',
  `sent_at`               DATETIME        DEFAULT NULL            COMMENT '基础通知发送成功时间',
  `created_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at`            DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record` (`trigger_record_id`),
  KEY `idx_assignee` (`assignee_open_id`),
  KEY `idx_status` (`status`),
  KEY `idx_status_retry` (`status`, `next_retry_at`),
  KEY `idx_analysis_status` (`analysis_status`),
  KEY `idx_attachment_status` (`attachment_status`, `attachment_retry_count`),
  KEY `idx_sent_at` (`sent_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售通知记录表';
