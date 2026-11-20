"""初始化业务库：执行建表脚本 + 插入测试数据（替代原 011_seed_test_data.sql）。

执行内容：
    1. 自动创建业务库（库名取 .env 的 MYSQL_DATABASE，默认 agent_calling，utf8mb4）
    2. 依序执行 sql/ 下的建表脚本（001~008、010），每张表建好后终端输出提示
    3. 插入 3 家企业完整链路测试数据（企业 → 联系人 → 外呼 → 通话 → AI 分析 → 通知 → 同步）
    4. 可重复执行：建表用 IF NOT EXISTS（已存在则跳过），测试数据按唯一键 upsert

用法：
    .venv\\Scripts\\python.exe sql\\000_init_db.py             # 建表 + 插测试数据
    .venv\\Scripts\\python.exe sql\\000_init_db.py --recreate  # 先删旧表再重建（旧数据不保留）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pymysql  # noqa: E402

from app.core.config import get_env  # noqa: E402

SQL_DIR = PROJECT_ROOT / "sql"

# 测试数据：3 家企业完整链路（原 011_seed_test_data.sql，按表分段；r""" 保留 ASR 中的 \n 转义）
SEED_SECTIONS: list[tuple[str, str, str]] = [
    ("import_batch", "导入批次 1 条", r"""
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
"""),
    ("customer_profile", "企业档案 3 家", r"""
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
"""),
    ("customer_contact", "联系人号码 4 条（企业 A 两个号码）", r"""
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
"""),
    ("outbound_job", "外呼任务 1 个", r"""
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
"""),
    ("call_record", "通话记录 3 通（对应 3 家企业）", r"""
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
"""),
    ("agent_analysis", "AI 分析 3 条（每通一条）", r"""
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
"""),
    ("dispatch_notice", "销售通知 2 条（空号不通知）", r"""
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
"""),
    ("sync_log", "同步日志 1 条", r"""
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
"""),
]


def split_statements(sql_text: str) -> list[str]:
    """去掉整行注释，按换行还原语句后按分号切分（本目录 SQL 无行内注释与字符串分号）。"""
    lines = [ln for ln in sql_text.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def extract_table(stmt: str) -> str | None:
    """从语句中提取 CREATE TABLE 的表名，非建表语句返回 None。"""
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`(\w+)`", stmt, re.I)
    return m.group(1) if m else None


def extract_table_comment(stmt: str) -> str:
    """提取建表语句末尾的表注释，如 企业档案主表。"""
    m = re.search(r"\)\s*ENGINE=.*?COMMENT='([^']*)'", stmt, re.S)
    return m.group(1) if m else ""


def load_ddl(path: Path) -> list[tuple[str, str | None]]:
    """读取建表脚本，返回 (语句, 表名) 列表。"""
    text = path.read_text(encoding="utf-8")
    return [(stmt, extract_table(stmt)) for stmt in split_statements(text)]


def build_config(database: str | None) -> dict:
    password = get_env("MYSQL_PASSWORD")
    if not password:
        raise SystemExit(
            "缺少 MYSQL_PASSWORD。请在 .env 中配置：\n"
            "  MYSQL_HOST=127.0.0.1\n"
            "  MYSQL_PORT=3306\n"
            "  MYSQL_USER=root\n"
            "  MYSQL_PASSWORD=***\n"
            "  MYSQL_DATABASE=agent_calling"
        )
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip() or "root",
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "connect_timeout": 8,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化业务库：建表 + 插入测试数据")
    parser.add_argument(
        "--recreate", action="store_true",
        help="先删除已存在的业务表再重建（旧数据不保留，测试数据重新插入）",
    )
    parser.add_argument(
        "--no-seed", action="store_true",
        help="只建表，不插入 3 家测试企业数据（生产部署用）",
    )
    args = parser.parse_args()

    database = get_env("MYSQL_DATABASE").strip() or "agent_calling"
    ddl_files = sorted(SQL_DIR.glob("0*.sql"))
    if not ddl_files:
        print(f"[错误] {SQL_DIR} 下未找到建表脚本（001~010）")
        return 1

    try:
        conn = pymysql.connect(**build_config(None))
    except pymysql.MySQLError as exc:
        print(f"[连接失败] {exc}")
        return 1

    try:
        # 1. 确保业务库存在
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        conn.select_db(database)
        print(f"=== 初始化业务库 {database}（MySQL {version}） ===")

        # 2. --recreate：逆序删除旧表
        if args.recreate:
            all_tables: list[str] = []
            for path in ddl_files:
                all_tables.extend(t for _, t in load_ddl(path) if t)
            with conn.cursor() as cur:
                for table in reversed(all_tables):
                    cur.execute(f"DROP TABLE IF EXISTS `{table}`")
                    print(f"  - 已删除旧表：{table}")
            print("! --recreate 模式：旧表已清空，开始重建\n")

        # 3. 依序建表，每张表输出提示
        total = len(ddl_files)
        for idx, path in enumerate(ddl_files, 1):
            for stmt, table in load_ddl(path):
                with conn.cursor() as cur:
                    if table:
                        exists = cur.execute("SHOW TABLES LIKE %s", (table,)) > 0
                    cur.execute(stmt)
                    if table:
                        comment = extract_table_comment(stmt)
                        tag = f"（{comment}）" if comment else ""
                        state = "已存在，跳过" if exists else "建表完成"
                        print(f"[{idx}/{total}] {path.name} → 表 {table}{tag} {state}")

        # 4. 插入测试数据（按唯一键 upsert，可重复执行）
        if args.no_seed:
            print("\n=== 已跳过测试数据（--no-seed） ===")
        else:
            print(f"\n=== 插入测试数据（{len(SEED_SECTIONS)} 段） ===")
            for table, label, sql in SEED_SECTIONS:
                with conn.cursor() as cur:
                    cur.execute(sql)
                print(f"✓ 测试数据 {table}（{label}）写入完成")
        conn.commit()

        # 5. 汇总
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]
        print(f"\n=== 完成：业务库 {database} 共 {len(tables)} 张表 ===")
        for name in tables:
            print(f"  - {name}")
        return 0
    except pymysql.MySQLError as exc:
        print(f"\n[执行失败] {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
