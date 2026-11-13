"""同步飞书通讯录到本地 feishu_user 表。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\sync_feishu_users.py
    .\\.venv\\Scripts\\python.exe scripts\\sync_feishu_users.py --dry-run

说明：只同步「应用通讯录可见范围」内的成员。部门字段当前留空。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients.feishu_api import list_all_users  # noqa: E402
from app.repositories.feishu_user_repo import (  # noqa: E402
    count_members,
    mark_missing_deleted,
    upsert_users,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只拉取不写库")
    args = parser.parse_args()

    print("拉取飞书通讯录…")
    try:
        users = list_all_users()
    except Exception as exc:
        print(f"❌ 拉取失败：{exc}")
        return 1

    print(f"✅ 拉到 {len(users)} 个成员")
    for user in users:
        print(f"   - {user.get('name') or '(无名)'}  {user.get('open_id')}")

    if args.dry_run:
        print("\n--dry-run：未写入数据库")
        return 0

    try:
        written = upsert_users(users)
        print(f"✅ 写入/更新 {written} 条")

        removed = mark_missing_deleted([u["open_id"] for u in users if u.get("open_id")])
        if removed:
            print(f"✅ 标记 {removed} 个成员为已删除（不在可见范围/已离职）")

        print(f"📊 当前在职成员数：{count_members()}")
    except Exception as exc:
        print(f"❌ 写库失败：{exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
