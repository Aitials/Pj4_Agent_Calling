-- ============================================================
-- 企业档案主表（V2 + 企查查字段适配 V1.1）
-- 数据库：MySQL 8.0+，字符集 utf8mb4
-- 变更说明：
--   1. 旧表按手机号建模，已不适用；先重命名为 customer_profile_legacy_20261116 备份
--   2. 新表以统一社会信用代码 credit_code 为唯一业务键
--   3. 号码下沉到 customer_contact 从表，一个企业可有多个号码
--   4. V1.1（2026-11-16）企查查导出字段适配：新增登记状态、核准日期、省市区拆分、
--      纳税人识别号、注册号、组织机构代码、参保人数、企业类型、曾用名、英文名、
--      网址、经营范围；新增可选索引 idx_customer_location、idx_registration_status
--      （对应文档 §3.1 / §4.4）
-- 可重复执行：已备份则跳过重命名，已建表则跳过建表
-- ============================================================
USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `customer_profile` (
  `id`                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `credit_code`         VARCHAR(32)     NOT NULL                COMMENT '统一社会信用代码，企业唯一业务键',
  `company_name`        VARCHAR(200)    NOT NULL                COMMENT '企业名称',
  `original_name`       TEXT            NULL                    COMMENT '曾用名，企查查 OriginalName 原始文本',
  `english_name`        VARCHAR(255)    DEFAULT NULL            COMMENT '英文名，企查查 EnglishName',
  `legal_person`        VARCHAR(64)     DEFAULT NULL            COMMENT '法人姓名',
  `registration_status` VARCHAR(32)     DEFAULT NULL            COMMENT '企业登记状态，如存续、注销、吊销（企查查 Status）',
  `industry`            VARCHAR(64)     DEFAULT NULL            COMMENT '行业',
  `econ_kind`           VARCHAR(128)    DEFAULT NULL            COMMENT '企业类型（企查查 EconKind）',
  `business_scope`      TEXT            NULL                    COMMENT '经营范围（企查查 Scope），内容较长用 TEXT 避免截断',
  `region`              VARCHAR(64)     DEFAULT NULL            COMMENT '所在地区，如 浙江省杭州市',
  `province`            VARCHAR(32)     DEFAULT NULL            COMMENT '所属省份（企查查 Province）',
  `city`                VARCHAR(32)     DEFAULT NULL            COMMENT '所属城市（企查查 City）',
  `county`              VARCHAR(32)     DEFAULT NULL            COMMENT '所属区县（企查查 County）',
  `address`             VARCHAR(255)    DEFAULT NULL            COMMENT '企业地址',
  `website`             VARCHAR(512)    DEFAULT NULL            COMMENT '企业网址（企查查 WebSite）',
  `registered_capital`  VARCHAR(64)     DEFAULT NULL            COMMENT '注册资本，保留原始展示文本',
  `established_at`      DATE            DEFAULT NULL            COMMENT '成立日期',
  `approved_at`         DATE            DEFAULT NULL            COMMENT '核准日期（企查查 CheckDate）',
  `tax_no`              VARCHAR(64)     DEFAULT NULL            COMMENT '纳税人识别号（企查查 TaxNo）',
  `registration_no`     VARCHAR(64)     DEFAULT NULL            COMMENT '注册号（企查查 No）',
  `org_code`            VARCHAR(32)     DEFAULT NULL            COMMENT '组织机构代码（企查查 OrgNo）',
  `insured_count`       INT UNSIGNED    DEFAULT NULL            COMMENT '参保人数（企查查 InsuredCount），无法解析时存 NULL 不写 0',

  `contact_count`       INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '关联号码数量，冗余统计',
  `call_times`          INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '企业累计外呼次数',
  `last_call_at`        DATETIME        DEFAULT NULL            COMMENT '最近一次外呼时间',
  `best_intent_level`   VARCHAR(16)     DEFAULT NULL            COMMENT '历史最高意向：HIGH/MEDIUM/LOW/INVALID',
  `last_intent_level`   VARCHAR(16)     DEFAULT NULL            COMMENT '最近一次通话的 AI 意向',

  `notify_status`       TINYINT         NOT NULL DEFAULT 0      COMMENT '通知销售状态：0未通知 1已通知 2通知失败',
  `last_notified_at`    DATETIME        DEFAULT NULL            COMMENT '最近一次通知时间',

  `source_channel`      VARCHAR(32)     NOT NULL DEFAULT 'QICHACHA' COMMENT '数据来源：QICHACHA/MANUAL',
  `first_batch_no`      VARCHAR(32)     DEFAULT NULL            COMMENT '首次导入批次号',
  `note`                VARCHAR(512)    DEFAULT NULL            COMMENT '备注',
  `is_blacklist`        TINYINT         NOT NULL DEFAULT 0      COMMENT '企业级黑名单：0否 1是',

  `raw_json`            JSON            DEFAULT NULL            COMMENT '企查查原始行或合并后的原始数据（含邮箱 MoreEmailList）',
  `created_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '本地创建时间',
  `updated_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '本地更新时间',
  `last_synced_at`      DATETIME        DEFAULT NULL            COMMENT '最近一次同步/合并时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_credit_code` (`credit_code`),
  KEY `idx_company_name` (`company_name`),
  KEY `idx_region` (`region`),
  KEY `idx_customer_location` (`province`, `city`),
  KEY `idx_registration_status` (`registration_status`),
  KEY `idx_best_intent` (`best_intent_level`),
  KEY `idx_notify_status` (`notify_status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='企业档案主表';
