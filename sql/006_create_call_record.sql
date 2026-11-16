-- ============================================================
-- 通话记录表
-- 保存一知返回的通话明细、录音地址与 ASR 转写
-- 幂等键：record_id（缺失时用 号码+时间+任务号 生成稳定哈希）
-- ============================================================

USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `call_record` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `record_id`          VARCHAR(64)     NOT NULL                COMMENT '一知通话记录 ID，唯一幂等键',
  `credit_code`        VARCHAR(32)     NOT NULL                COMMENT '所属企业信用代码，冗余便于企业维度查询',
  `customer_phone`     VARCHAR(32)     NOT NULL                COMMENT '被叫客户号码',
  `local_job_no`       VARCHAR(32)     DEFAULT NULL            COMMENT '关联我方任务编号',
  `provider_job_id`    VARCHAR(64)     DEFAULT NULL            COMMENT '一知任务 ID',
  `call_time`          DATETIME        NOT NULL                COMMENT '通话开始时间',
  `duration`           INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '通话时长，单位秒',
  `call_status`        VARCHAR(24)     NOT NULL                COMMENT 'ANSWERED/NO_ANSWER/INVALID/REJECTED/FAILED',
  `status_raw`         VARCHAR(64)     DEFAULT NULL            COMMENT '一知原始状态码/状态文本',
  `supplier_level`     VARCHAR(16)     DEFAULT NULL            COMMENT '一知返回的原始意向标签',
  `record_url`         VARCHAR(512)    DEFAULT NULL            COMMENT '录音地址',
  `local_record_path`  VARCHAR(512)    DEFAULT NULL            COMMENT '本地留存路径，可选',
  `asr_transcript`     TEXT            DEFAULT NULL            COMMENT '完整转写，格式 [机器人] xxx\\n[客户] yyy',
  `first_message`      VARCHAR(256)    DEFAULT NULL            COMMENT '机器人开场白，用于列表预览',
  `last_message`       VARCHAR(256)    DEFAULT NULL            COMMENT '客户最后一句，用于列表预览',
  `synced_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地同步入库时间',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `raw_json`           JSON            DEFAULT NULL            COMMENT '一知通话记录原始报文',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_id` (`record_id`),
  KEY `idx_phone_time` (`customer_phone`, `call_time`),
  KEY `idx_credit_time` (`credit_code`, `call_time`),
  KEY `idx_call_time` (`call_time`),
  KEY `idx_job` (`local_job_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='通话记录表';
