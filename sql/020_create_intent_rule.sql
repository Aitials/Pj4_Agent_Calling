-- ============================================================
-- 意向等级本地分级映射
-- 从一知意向标签组同步 code/name，本地标注 HIGH/MEDIUM/LOW/NONE 与是否通知
-- 不同标签组的 code 含义不同，必须按 (tag_id, code) 联合确定
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `intent_level_rule` (
  `id`                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `intent_level_tag_id` BIGINT          NOT NULL                COMMENT '一知意向标签组 ID，如 1823',
  `intent_level_code`   INT             NOT NULL                COMMENT '组内 code，如 0',
  `code_name`           VARCHAR(64)     NOT NULL                COMMENT '一知侧名称，同步覆盖',
  `local_level`         VARCHAR(8)      NOT NULL DEFAULT 'NONE' COMMENT '本地分级：HIGH/MEDIUM/LOW/NONE',
  `notify`              TINYINT(1)      NOT NULL DEFAULT 0      COMMENT '是否触发高意向通知',
  `enabled`             TINYINT(1)      NOT NULL DEFAULT 1      COMMENT '是否启用',
  `updated_at`          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_tag_code` (`intent_level_tag_id`, `intent_level_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='意向等级本地分级映射';
