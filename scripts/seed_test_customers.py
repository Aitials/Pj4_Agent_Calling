"""向 customer_profile 写入 5 条测试数据（幂等，按 phone upsert）。

用法：
    .venv\\Scripts\\python.exe scripts\\seed_test_customers.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.customer_repo import DatabaseError, connect  # noqa: E402

TEST_CUSTOMERS = [
    {
        "phone": "13800001111",
        "name": "张三",
        "company_name": "启明教育科技",
        "industry": "教育",
        "crm_cate": 1,
        "phone_level": 1,
        "call_times": 3,
        "task_name": "11月新客外呼",
        "note": "对价格敏感，意向明确",
        "source_created_at": "2026-11-10 14:20:00",
    },
    {
        "phone": "13900002222",
        "name": "张小明",
        "company_name": "恒信金融服务",
        "industry": "金融",
        "crm_cate": 2,
        "phone_level": 2,
        "call_times": 2,
        "task_name": "11月新客外呼",
        "note": "需二次沟通方案",
        "source_created_at": "2026-11-11 09:05:00",
    },
    {
        "phone": "13700003333",
        "name": "李四",
        "company_name": "远航制造集团",
        "industry": "制造业",
        "crm_cate": 4,
        "phone_level": 1,
        "call_times": 5,
        "task_name": "10月老客回访",
        "note": "已签单",
        "source_created_at": "2026-11-09 16:40:00",
    },
    {
        "phone": "13600004444",
        "name": "王五",
        "company_name": "云图网络科技",
        "industry": "互联网",
        "crm_cate": 5,
        "phone_level": 4,
        "call_times": 0,
        "task_name": "暂无任务",
        "note": "明确表示不需要",
        "source_created_at": "2026-11-08 11:12:00",
    },
    {
        "phone": "13500005555",
        "name": "赵六",
        "company_name": "瑞泰医疗器械",
        "industry": "医疗器械",
        "crm_cate": 7,
        "phone_level": 5,
        "call_times": 1,
        "task_name": "11月新客外呼",
        "note": "两次未接",
        "source_created_at": "2026-11-11 18:30:00",
    },
]

SQL = """
INSERT INTO `customer_profile`
  (`phone`, `name`, `company_name`, `industry`, `crm_cate`, `phone_level`,
   `call_times`, `task_name`, `note`, `source_created_at`, `last_synced_at`, `raw_json`)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
ON DUPLICATE KEY UPDATE
  `name` = VALUES(`name`),
  `company_name` = VALUES(`company_name`),
  `industry` = VALUES(`industry`),
  `crm_cate` = VALUES(`crm_cate`),
  `phone_level` = VALUES(`phone_level`),
  `call_times` = VALUES(`call_times`),
  `task_name` = VALUES(`task_name`),
  `note` = VALUES(`note`),
  `source_created_at` = VALUES(`source_created_at`),
  `last_synced_at` = NOW(),
  `raw_json` = VALUES(`raw_json`)
"""


def main() -> int:
    try:
        conn = connect()
    except DatabaseError as exc:
        print(f"[失败] {exc}")
        return 1

    rows = [
        (
            item["phone"],
            item["name"],
            item["company_name"],
            item["industry"],
            item["crm_cate"],
            item["phone_level"],
            item["call_times"],
            item["task_name"],
            item["note"],
            item["source_created_at"],
            json.dumps(item, ensure_ascii=False),
        )
        for item in TEST_CUSTOMERS
    ]

    try:
        with conn.cursor() as cur:
            affected = cur.executemany(SQL, rows)
        conn.commit()
        print(f"executemany 影响行数：{affected}")
        print(f"已写入/更新 {len(rows)} 条测试客户数据：")
        for item in TEST_CUSTOMERS:
            print(f"  {item['phone']}  {item['name']}  {item['company_name']}")
    except Exception as exc:  # noqa: BLE001
        print(f"[写入失败] {type(exc).__name__}: {exc}")
        print(f"时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
