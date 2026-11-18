-- ============================================================
-- 轮流分配游标（单行）
-- 记录上次分配到的销售，下一次从名单里取下一位
-- ============================================================

USE `agent_calling`;

CREATE TABLE IF NOT EXISTS `dispatch_assignee_cursor` (
  `id`           TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '固定为 1，单行游标',
  `last_open_id` VARCHAR(64) DEFAULT NULL           COMMENT '上次分配到的销售 open_id',
  `updated_at`   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='轮流分配游标';

INSERT INTO `dispatch_assignee_cursor` (`id`, `last_open_id`)
VALUES (1, NULL)
ON DUPLICATE KEY UPDATE `id` = `id`;
