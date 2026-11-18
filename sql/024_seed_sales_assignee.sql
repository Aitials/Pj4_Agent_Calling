-- ============================================================
-- 测试销售种子
-- 仅用于联调；正式名单通过 sales_assignee 维护
-- ============================================================

USE `agent_calling`;

INSERT INTO `sales_assignee` (`open_id`, `name`, `enabled`, `sort_order`)
VALUES ('ou_661f720af150e164ba96d74e59ea80e8', '壕z', 1, 1)
ON DUPLICATE KEY UPDATE
  `name` = VALUES(`name`),
  `enabled` = 1;
