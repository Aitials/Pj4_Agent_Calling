"""从一知同步意向标签组到 intent_level_rule。

用法：
    python scripts/sync_intent_rules.py
    python scripts/sync_intent_rules.py --tag-id 1823
"""

from __future__ import annotations

import argparse
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.clients import aicc_call_record  # noqa: E402
from app.repositories import intent_rule_repo  # noqa: E402

# 1823 组默认分级：A/B 通知，C 已加微信不通知，D/E 不通知
DEFAULT_LEVELS: dict[int, tuple[str, int]] = {
    0: ("HIGH", 1),
    1: ("MEDIUM", 1),
    2: ("HIGH", 0),
    3: ("LOW", 0),
    4: ("NONE", 0),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="同步一知意向标签组到本地规则表")
    parser.add_argument("--tag-id", type=int, default=1823, help="意向标签组 ID，默认 1823")
    args = parser.parse_args()

    try:
        groups = aicc_call_record.list_intent_groups()
    except Exception as exc:  # noqa: BLE001
        print(f"拉取意向标签组失败：{exc}")
        return 1

    group = next(
        (g for g in groups if int(g.get("intentLevelTagId") or -1) == args.tag_id),
        None,
    )
    if not group:
        print(f"未找到标签组 {args.tag_id}，可用标签组：")
        for item in groups[:30]:
            print(f"  - {item.get('intentLevelTagId')}  {item.get('name')}")
        return 1

    details = group.get("details") or group.get("intentLevelTagDetailList") or []
    rows = []
    for item in details:
        code = item.get("code")
        name = item.get("name")
        if code is None or not name:
            continue
        level, notify = DEFAULT_LEVELS.get(int(code), ("NONE", 0))
        rows.append(
            {
                "code": int(code),
                "name": str(name),
                "local_level": level,
                "notify": notify,
            }
        )

    written = intent_rule_repo.upsert_rules(args.tag_id, rows)
    print(f"标签组 {args.tag_id}（{group.get('name')}）同步 {written} 档：")

    final = intent_rule_repo.list_rules(args.tag_id)
    for row in final:
        print(
            f"  {row['intent_level_code']:>2}  {row['code_name']:<20} "
            f"{row['local_level']:<6} notify={row['notify']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
