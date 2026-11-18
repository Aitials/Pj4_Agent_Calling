-- ============================================================
-- 销售名单（手工维护）
-- 与 feishu_user 解耦：通讯录可能为空或包含非销售人员
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `sales_assignee` (
  `open_id`    VARCHAR(64) NOT NULL                COMMENT '飞书 open_id',
  `name`       VARCHAR(64) DEFAULT NULL            COMMENT '销售姓名',
  `enabled`    TINYINT(1)  NOT NULL DEFAULT 1      COMMENT '是否参与轮转',
  `sort_order` INT         NOT NULL DEFAULT 0      COMMENT '轮转顺序，越小越靠前',
  `created_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`open_id`),
  KEY `idx_enabled_sort` (`enabled`, `sort_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售名单';
