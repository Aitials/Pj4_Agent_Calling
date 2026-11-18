-- ============================================================
-- 通话记录表
-- 保存一知返回的通话明细、录音地址与 ASR 转写
-- 字段对齐《一知智能AICC开放平台接口文档（官方）》通话记录回调
-- 幂等键：record_id（缺失时用 号码+时间+任务号 生成稳定哈希）
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `call_record` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `record_id`          VARCHAR(64)     NOT NULL                COMMENT '一知通话记录 ID（callRecordId），唯一幂等键',
  `credit_code`        VARCHAR(32)     DEFAULT NULL            COMMENT '所属企业信用代码；号码未匹配到企业时为空',
  `customer_name`      VARCHAR(128)    DEFAULT NULL            COMMENT '一知回调里的客户名称',
  `customer_phone`     VARCHAR(32)     NOT NULL                COMMENT '被叫客户号码',
  `local_job_no`       VARCHAR(32)     DEFAULT NULL            COMMENT '关联我方任务编号',
  `provider_job_id`    VARCHAR(64)     DEFAULT NULL            COMMENT '一知任务 ID（robotCallJobId）',
  `call_time`          DATETIME        NOT NULL                COMMENT '通话开始时间',
  `duration`           INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '通话时长，单位秒',
  `call_status`        VARCHAR(24)     NOT NULL                COMMENT '归一状态：ANSWERED/NO_ANSWER/INVALID/REJECTED/FAILED/UNKNOWN',
  `status_raw`         VARCHAR(64)     DEFAULT NULL            COMMENT '一知原始 resultStatus，保留原值便于回溯',
  `supplier_level`     VARCHAR(64)     DEFAULT NULL            COMMENT '一知意向标签名称快照，仅供展示',
  `intent_level_tag_id` BIGINT UNSIGNED DEFAULT NULL           COMMENT '一知意向标签组 ID',
  `intent_level_code`  INT             DEFAULT NULL            COMMENT '一知意向标签 code，与标签组 ID 唯一确定标签',
  `hangup_by`          VARCHAR(24)     DEFAULT NULL            COMMENT '挂断方：REMOTE_HANGUP/INITIAL_HANGUP/CS_HANGUP/OTHER_HANGUP',
  `chat_round`         INT UNSIGNED    DEFAULT NULL            COMMENT '通话轮次',
  `ring_duration`      INT UNSIGNED    DEFAULT NULL            COMMENT '振铃时长，单位秒',
  `analysis_basis`     VARCHAR(512)    DEFAULT NULL            COMMENT '一知意向判定依据',
  `customer_concern`   VARCHAR(512)    DEFAULT NULL            COMMENT '客户关注点，逗号分隔',
  `record_url`         VARCHAR(512)    DEFAULT NULL            COMMENT '录音地址，一知默认保留 90 天',
  `record_expire_at`   DATE            DEFAULT NULL            COMMENT '录音预计过期日期，用于提醒转存',
  `local_record_path`  VARCHAR(512)    DEFAULT NULL            COMMENT '本地留存路径，可选',
  `asr_transcript`     MEDIUMTEXT      DEFAULT NULL            COMMENT '完整转写，格式 [机器人] xxx\\n[客户] yyy',
  `first_message`      VARCHAR(256)    DEFAULT NULL            COMMENT '机器人开场白，用于列表预览',
  `last_message`       VARCHAR(256)    DEFAULT NULL            COMMENT '客户最后一句，用于列表预览',
  `properties`         JSON            DEFAULT NULL            COMMENT '导入时回传的自定义变量，建议含 credit_code/local_job_no',
  `dynamic_properties` JSON            DEFAULT NULL            COMMENT '通话过程中收集的动态变量',
  `callback_data_type` VARCHAR(64)     DEFAULT NULL            COMMENT '回调 dataType，区分新旧版',
  `synced_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地同步入库时间',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `raw_json`           JSON            DEFAULT NULL            COMMENT '一知通话记录原始报文',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_record_id` (`record_id`),
  KEY `idx_phone_time` (`customer_phone`, `call_time`),
  KEY `idx_credit_time` (`credit_code`, `call_time`),
  KEY `idx_call_time` (`call_time`),
  KEY `idx_job` (`local_job_no`),
  KEY `idx_provider_job_time` (`provider_job_id`, `call_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='通话记录表';
