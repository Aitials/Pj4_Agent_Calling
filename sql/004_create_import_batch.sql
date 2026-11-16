-- ============================================================
-- 名单导入批次表
-- 每次导入企查查 Excel 记录一条，用于统计结果与定位错误
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `import_batch` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `batch_no`         VARCHAR(32)     NOT NULL                COMMENT '批次号，唯一，如 IMP20261116-01',
  `source_channel`   VARCHAR(32)     NOT NULL DEFAULT 'QICHACHA' COMMENT '来源：QICHACHA/MANUAL',
  `file_name`        VARCHAR(255)    NOT NULL                COMMENT '原始文件名',
  `total_rows`       INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '文件总行数',
  `company_count`    INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '归并后企业数',
  `contact_count`    INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '实际入库号码数',
  `duplicated_count` INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '重复行数（文件内 + 库内）',
  `invalid_count`    INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '无效行数，如信用代码缺失、号码非法',
  `conflict_count`   INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '号码归属冲突数',
  `blacklist_count`  INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '命中黑名单并过滤的数量',
  `error_file`       VARCHAR(255)    DEFAULT NULL            COMMENT '错误明细文件路径',
  `operator`         VARCHAR(64)     DEFAULT NULL            COMMENT '导入操作人',
  `status`           VARCHAR(16)     NOT NULL DEFAULT 'RUNNING' COMMENT '导入状态：RUNNING/SUCCESS/PARTIAL/FAILED',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '导入时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_batch_no` (`batch_no`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='名单导入批次表';
