-- ============================================================
-- 销售对话历史表
-- 保存销售与 Agent 的消息，用于短期上下文拼接
-- 保留策略：每会话保留最近 200 条；回复前读取最近 10 轮
-- ============================================================

USE `pj5_agent_calling`;

CREATE TABLE IF NOT EXISTS `chat_history` (
  `id`               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '本地自增主键',
  `conversation_id`  VARCHAR(64)     NOT NULL                COMMENT '会话标识，飞书 chat_id 或 open_id',
  `open_id`          VARCHAR(64)     NOT NULL                COMMENT '销售成员 open_id',
  `role`             VARCHAR(16)     NOT NULL                COMMENT '角色：user销售 / assistant机器人',
  `content`          TEXT            NOT NULL                COMMENT '消息内容',
  `token_len`        INT UNSIGNED    DEFAULT NULL            COMMENT '估算 token 数，用于上下文裁剪',
  `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '消息时间',

  PRIMARY KEY (`id`),
  KEY `idx_conversation_time` (`conversation_id`, `created_at`),
  KEY `idx_open_id` (`open_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='销售对话历史表';
