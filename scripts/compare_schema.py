"""对比 customer_profile 表结构 vs 好客搜接口返回字段。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clients.haokesou import collect_customers  # noqa: E402
from app.repositories.customer_repo import connect  # noqa: E402


def db_cols():
    c = connect()
    with c, c.cursor() as cur:
        cur.execute("DESCRIBE customer_profile")
        return {r["Field"]: r for r in cur.fetchall()}


def hks_fields():
    rows = collect_customers(limit=2, max_pages=1)
    if not rows:
        return {}
    sample = rows[0]
    print("\n[好客搜接口示例记录（前 1 条）]")
    print(json.dumps(sample, ensure_ascii=False, indent=2))
    return sample


def main():
    db = db_cols()
    hks = hks_fields()

    print("\n[customer_profile 表结构]")
    for field, meta in db.items():
        print(f"  {field:22} {meta['Type']:20} {meta['Null']} {meta['Key']}")

    db_field_set = set(db.keys())
    hks_field_set = set(hks.keys())

    only_db = sorted(db_field_set - hks_field_set)
    only_hks = sorted(hks_field_set - db_field_set)
    both = sorted(db_field_set & hks_field_set)

    print("\n=== 对比结果 ===")
    print(f"DB 独有（不在接口里）：{only_db}")
    print(f"接口独有（不在 DB 里）：{only_hks}")
    print(f"两边都有：{both}")


if __name__ == "__main__":
    main()
