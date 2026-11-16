-- ============================================================
-- 测试数据（3 家企业完整链路）
-- 用途：验证企业 → 联系人 → 外呼任务 → 通话 → AI 分析 → 销售通知 → 对话记忆
-- 说明：企业名与信用代码均为虚构测试数据；飞书 open_id 取自本地 feishu_user
-- 可重复执行：按各表唯一键 upsert，不会产生重复记录
-- ============================================================

USE `pj5_agent_calling`;

-- ---------- 1. 导入批次 ----------
INSERT INTO `import_batch`
  (`batch_no`, `source_channel`, `file_name`, `total_rows`, `company_count`, `contact_count`,
   `duplicated_count`, `invalid_count`, `conflict_count`, `blacklist_count`,
   `error_file`, `operator`, `status`, `created_at`)
VALUES
  ('IMP20261116-01', 'QICHACHA', 'qichacha_20261116.xlsx', 3, 3, 4, 0, 0, 0, 0,
   NULL, '壕z', 'SUCCESS', '2026-11-16 09:30:00')
ON DUPLICATE KEY UPDATE
  `company_count` = VALUES(`company_count`),
  `contact_count` = VALUES(`contact_count`),
  `status` = VALUES(`status`);

-- ---------- 2. 企业档案（3 家） ----------
INSERT INTO `customer_profile`
  (`credit_code`, `company_name`, `legal_person`, `industry`, `region`, `address`,
   `registered_capital`, `established_at`, `contact_count`, `call_times`, `last_call_at`,
   `best_intent_level`, `last_intent_level`, `notify_status`, `last_notified_at`,
   `source_channel`, `first_batch_no`, `note`, `is_blacklist`)
VALUES
  ('91330106MA2TST001A', '杭州云启信息技术有限公司', '张明', '软件和信息技术服务业',
   '浙江省杭州市', '杭州市西湖区文三路 100 号', '500万元人民币', '2018-03-15',
   2, 1, '2026-11-16 10:20:00', 'HIGH', 'HIGH', 1, '2026-11-16 10:25:00',
   'QICHACHA', 'IMP20261116-01', '企查查导入，首轮外呼高意向', 0),
  ('91330106MA2TST001B', '苏州恒达机械制造有限公司', '李强', '通用设备制造业',
   '江苏省苏州市', '苏州市吴中区木渎镇金枫路 88 号', '1000万元人民币', '2015-07-01',
   1, 1, '2026-11-16 10:22:00', 'MEDIUM', 'MEDIUM', 1, '2026-11-16 10:26:00',
   'QICHACHA', 'IMP20261116-01', '现用竞品，需老板决策', 0),
  ('91330106MA2TST001C', '宁波蓝海贸易有限公司', '王芳', '批发业',
   '浙江省宁波市', '宁波市鄞州区首南街道泰康中路 66 号', '200万元人民币', '2020-11-20',
   1, 1, '2026-11-16 10:24:00', 'INVALID', 'INVALID', 0, NULL,
   'QICHACHA', 'IMP20261116-01', '首轮外呼为空号，待核查号码', 0)
ON DUPLICATE KEY UPDATE
  `company_name` = VALUES(`company_name`),
  `contact_count` = VALUES(`contact_count`),
  `call_times` = VALUES(`call_times`),
  `last_call_at` = VALUES(`last_call_at`),
  `best_intent_level` = VALUES(`best_intent_level`),
  `last_intent_level` = VALUES(`last_intent_level`),
  `notify_status` = VALUES(`notify_status`),
  `last_notified_at` = VALUES(`last_notified_at`);

-- ---------- 3. 联系人/号码（4 条：A 两个号码） ----------
INSERT INTO `customer_contact`
  (`credit_code`, `phone`, `phone_type`, `contact_name`, `is_primary`, `is_blacklist`,
   `block_reason`, `call_times`, `last_call_at`, `last_call_status`, `ai_intent_level`,
   `source_channel`, `import_batch_no`)
VALUES
  ('91330106MA2TST001A', '13800000001', 'MOBILE', '张明', 1, 0,
   NULL, 1, '2026-11-16 10:20:00', 'ANSWERED', 'HIGH', 'QICHACHA', 'IMP20261116-01'),
  ('91330106MA2TST001A', '0571-88880001', 'LANDLINE', '张明', 0, 0,
   NULL, 0, NULL, NULL, NULL, 'QICHACHA', 'IMP20261116-01'),
  ('91330106MA2TST001B', '13900000002', 'MOBILE', '李强', 1, 0,
   NULL, 1, '2026-11-16 10:22:00', 'ANSWERED', 'MEDIUM', 'QICHACHA', 'IMP20261116-01'),
  ('91330106MA2TST001C', '13700000003', 'MOBILE', '王芳', 1, 0,
   'EMPTY_NUMBER', 1, '2026-11-16 10:24:00', 'INVALID', 'INVALID', 'QICHACHA', 'IMP20261116-01')
ON DUPLICATE KEY UPDATE
  `credit_code` = VALUES(`credit_code`),
  `phone_type` = VALUES(`phone_type`),
  `contact_name` = VALUES(`contact_name`),
  `is_primary` = VALUES(`is_primary`),
  `block_reason` = VALUES(`block_reason`),
  `call_times` = VALUES(`call_times`),
  `last_call_at` = VALUES(`last_call_at`),
  `last_call_status` = VALUES(`last_call_status`),
  `ai_intent_level` = VALUES(`ai_intent_level`);

-- ---------- 4. 外呼任务 ----------
INSERT INTO `outbound_job`
  (`local_job_no`, `job_name`, `provider`, `provider_job_id`, `line_id`, `line_number`,
   `concurrency`, `robot_count`, `dialog_flow_id`, `batch_no`, `target_count`,
   `status`, `status_raw`, `plan_start_at`, `plan_end_at`, `started_at`)
VALUES
  ('JOB20261116-01', '企查查线索首轮外呼', 'YIZHI', 'yz-job-1001', '8891', '0571-99990000',
   1, 1, 'yz-bot-201', 'IMP20261116-01', 4,
   'RUNNING', 'RUNNING', '2026-11-16 10:00:00', '2026-11-16 18:00:00', '2026-11-16 10:00:05')
ON DUPLICATE KEY UPDATE
  `provider_job_id` = VALUES(`provider_job_id`),
  `line_id` = VALUES(`line_id`),
  `status` = VALUES(`status`),
  `status_raw` = VALUES(`status_raw`),
  `started_at` = VALUES(`started_at`);

-- ---------- 5. 通话记录（3 通，分别对应 3 家企业） ----------
INSERT INTO `call_record`
  (`record_id`, `credit_code`, `customer_phone`, `local_job_no`, `provider_job_id`,
   `call_time`, `duration`, `call_status`, `status_raw`, `supplier_level`,
   `record_url`, `asr_transcript`, `first_message`, `last_message`, `synced_at`)
VALUES
  ('YZ202611160001', '91330106MA2TST001A', '13800000001', 'JOB20261116-01', 'yz-job-1001',
   '2026-11-16 10:20:00', 86, 'ANSWERED', '1', 'A',
   'https://demo.example.com/rec/YZ202611160001.mp3',
   '[机器人] 您好，这里是杭州云启，请问是张经理吗？\n[客户] 我是，什么事？\n[机器人] 我们这边提供智能外呼系统，想了解您这边是否有人工外呼的需求？\n[客户] 有的，我们销售团队现在手动打，效率太低。你们怎么收费？\n[机器人] 按坐席和通话时长计费，可以把详细方案发您。\n[客户] 行，加我微信，明天下午三点后打我电话详聊。',
   '您好，这里是杭州云启，请问是张经理吗？',
   '行，加我微信，明天下午三点后打我电话详聊。',
   '2026-11-16 10:30:00'),
  ('YZ202611160002', '91330106MA2TST001B', '13900000002', 'JOB20261116-01', 'yz-job-1001',
   '2026-11-16 10:22:00', 54, 'ANSWERED', '1', 'B',
   'https://demo.example.com/rec/YZ202611160002.mp3',
   '[机器人] 您好，恒达机械吗？我们提供智能外呼系统。\n[客户] 现在我们用的别家，效果一般。\n[机器人] 我们支持通话转人工，也能做意向分级。\n[客户] 这个得问下老板，你先发资料过来吧。',
   '您好，恒达机械吗？我们提供智能外呼系统。',
   '这个得问下老板，你先发资料过来吧。',
   '2026-11-16 10:30:00'),
  ('YZ202611160003', '91330106MA2TST001C', '13700000003', 'JOB20261116-01', 'yz-job-1001',
   '2026-11-16 10:24:00', 0, 'INVALID', '5', 'F',
   NULL, NULL, NULL, NULL,
   '2026-11-16 10:30:00')
ON DUPLICATE KEY UPDATE
  `duration` = VALUES(`duration`),
  `call_status` = VALUES(`call_status`),
  `status_raw` = VALUES(`status_raw`),
  `supplier_level` = VALUES(`supplier_level`),
  `record_url` = VALUES(`record_url`),
  `asr_transcript` = VALUES(`asr_transcript`),
  `synced_at` = VALUES(`synced_at`);

-- ---------- 6. AI 分析（每通一条） ----------
INSERT INTO `agent_analysis`
  (`record_id`, `credit_code`, `customer_phone`, `intent_level`, `reason`, `quote`,
   `demand`, `budget`, `objection`, `blocked`, `block_reason`, `suggestion`,
   `best_follow_at`, `supplier_level`, `level_consistent`, `model`, `analyzed_at`)
VALUES
  ('YZ202611160001', '91330106MA2TST001A', '13800000001', 'HIGH',
   '客户主动确认有人工外呼需求，询问收费并约定回电时间，意向明确',
   '有的，我们销售团队现在手动打，效率太低。你们怎么收费？',
   '智能外呼系统，替代人工外呼', '未明确，需方案报价', '暂无',
   0, NULL, '明天下午三点后电话跟进，先发方案与报价', '明天下午3点后',
   'A', 1, 'glm-4.5-air', '2026-11-16 10:32:00'),
  ('YZ202611160002', '91330106MA2TST001B', '13900000002', 'MEDIUM',
   '现用竞品效果一般，有替换可能，但需老板决策，短期难推进',
   '现在我们用的别家，效果一般。这个得问下老板，你先发资料过来吧。',
   '替换现有外呼系统', '未提及', '需老板决策',
   0, NULL, '先发资料，三天内回访确认决策人态度', '3天后上午',
   'B', 1, 'glm-4.5-air', '2026-11-16 10:33:00'),
  ('YZ202611160003', '91330106MA2TST001C', '13700000003', 'INVALID',
   '号码为空号，未接通，无有效沟通内容',
   NULL, NULL, NULL, NULL,
   1, 'EMPTY_NUMBER', '不建议继续拨打，先核查号码有效性', NULL,
   'F', 1, 'glm-4.5-air', '2026-11-16 10:34:00')
ON DUPLICATE KEY UPDATE
  `intent_level` = VALUES(`intent_level`),
  `reason` = VALUES(`reason`),
  `quote` = VALUES(`quote`),
  `demand` = VALUES(`demand`),
  `blocked` = VALUES(`blocked`),
  `block_reason` = VALUES(`block_reason`),
  `suggestion` = VALUES(`suggestion`),
  `analyzed_at` = VALUES(`analyzed_at`);

-- ---------- 7. 销售通知（2 条：高/中意向各一条，空号不通知） ----------
INSERT INTO `dispatch_notice`
  (`credit_code`, `company_name`, `trigger_record_id`, `trigger_phone`,
   `assignee_open_id`, `assignee_name`, `feishu_task_guid`, `card_message_id`,
   `trigger_source`, `status`, `error_msg`, `sent_at`, `created_at`)
VALUES
  ('91330106MA2TST001A', '杭州云启信息技术有限公司', 'YZ202611160001', '13800000001',
   'ou_290272b2dcd7d52a1cdba52c79cc29d7', '陈炳为',
   'task-demo-0001', 'om_demo_card_0001', 'AI', 'SENT', NULL,
   '2026-11-16 10:25:00', '2026-11-16 10:25:00'),
  ('91330106MA2TST001B', '苏州恒达机械制造有限公司', 'YZ202611160002', '13900000002',
   'ou_661f720af150e164ba96d74e59ea80e8', '壕z',
   'task-demo-0002', 'om_demo_card_0002', 'AI', 'SENT', NULL,
   '2026-11-16 10:26:00', '2026-11-16 10:26:00')
ON DUPLICATE KEY UPDATE
  `company_name` = VALUES(`company_name`),
  `trigger_record_id` = VALUES(`trigger_record_id`),
  `feishu_task_guid` = VALUES(`feishu_task_guid`),
  `status` = VALUES(`status`),
  `sent_at` = VALUES(`sent_at`);

-- ---------- 8. 销售对话（同一会话 3 条，用于短期记忆测试） ----------
INSERT INTO `chat_history`
  (`id`, `conversation_id`, `open_id`, `role`, `content`, `token_len`, `created_at`)
VALUES
  (1, 'ou_290272b2dcd7d52a1cdba52c79cc29d7', 'ou_290272b2dcd7d52a1cdba52c79cc29d7',
   'user', '杭州云启这个客户什么情况？', 12, '2026-11-16 10:40:00'),
  (2, 'ou_290272b2dcd7d52a1cdba52c79cc29d7', 'ou_290272b2dcd7d52a1cdba52c79cc29d7',
   'assistant', '杭州云启信息技术有限公司，高意向。客户说销售团队现在手动打电话效率太低，主动问了收费，并约你明天下午三点后回电详聊。建议先发方案和报价。', 68, '2026-11-16 10:40:05'),
  (3, 'ou_290272b2dcd7d52a1cdba52c79cc29d7', 'ou_290272b2dcd7d52a1cdba52c79cc29d7',
   'user', '那明天记得提醒我', 9, '2026-11-16 10:41:00')
ON DUPLICATE KEY UPDATE
  `content` = VALUES(`content`),
  `created_at` = VALUES(`created_at`);

-- ---------- 9. 同步日志 ----------
INSERT INTO `sync_log`
  (`id`, `provider`, `interface_name`, `sync_start_time`, `sync_end_time`,
   `records_fetched`, `records_saved`, `status`, `error_msg`, `cursor_time`)
VALUES
  (1, 'YIZHI', 'call_record', '2026-11-16 10:30:00', '2026-11-16 10:30:12',
   3, 3, 'SUCCESS', NULL, '2026-11-16 10:24:00')
ON DUPLICATE KEY UPDATE
  `records_fetched` = VALUES(`records_fetched`),
  `records_saved` = VALUES(`records_saved`),
  `status` = VALUES(`status`),
  `cursor_time` = VALUES(`cursor_time`);
