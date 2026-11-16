-- ============================================================
-- 销售对话历史表（短期记忆）
--
-- 用途：Agent 调用前按 conversation_id 加载历史消息，
--       拼成 messages 列表传给 LLM；调用成功后成对回写
--       user + assistant 两条消息。
--
-- conversation_id 取值：
--   群聊  → 飞书 chat_id（按群隔离会话）
--   单聊  → 飞书 open_id（按人隔离会话，fallback）
--
-- token_len：入库时用 len(content)//4 粗略估算，
--            读取时按 max_tokens=4000 从新到旧累加裁剪。
--
-- 保留策略：每次写入后清理，单会话只保留最近 200 条。
-- ============================================================

USE `agent_calling`;

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
