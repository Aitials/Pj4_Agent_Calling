"""飞书主动能力验证脚本：换 open_id → 发消息 → 建任务。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\feishu_api_check.py --mobile 13800001111
    .\\.venv\\Scripts\\python.exe scripts\\feishu_api_check.py --open-id ou_xxx --text "测试"

只跑其中一步的话：
    --only resolve | text | card | task
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients import feishu_api  # noqa: E402


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mobile", default="", help="目标人员手机号，用于换 open_id")
    parser.add_argument("--open-id", dest="open_id", default="", help="直接指定 open_id")
    parser.add_argument("--text", default="测试消息：来自外呼 Agent 的主动推送", help="文本内容")
    parser.add_argument(
        "--only", choices=["resolve", "text", "card", "task", "all"], default="all"
    )
    args = parser.parse_args()

    if not args.mobile and not args.open_id:
        print("请至少提供 --mobile 或 --open-id")
        return 2

    open_id = args.open_id

    # 1) 手机号换 open_id
    if not open_id:
        step("1. 手机号换 open_id（需要 contact:user.id:readonly）")
        try:
            found = feishu_api.resolve_open_ids(mobiles=[args.mobile])
        except Exception as exc:
            print(f"❌ {exc}")
            return 1
        if not found:
            print(f"❌ 手机号 {args.mobile} 在飞书通讯录里查不到（未入企业 / 已离职 / 号码不对）")
            return 1
        open_id = found[0]["open_id"]
        print(f"✅ open_id = {open_id}")
        if args.only == "resolve":
            return 0

    # 2) 发文本
    if args.only in ("all", "text"):
        step("2. 主动发文本（需要 im:message:send_as_bot）")
        try:
            result = feishu_api.send_text(open_id, args.text)
            print(f"✅ 已发送 message_id = {result.get('message_id')}")
        except Exception as exc:
            print(f"❌ {exc}")
            return 1

    # 3) 发卡片
    if args.only in ("all", "card"):
        step("3. 主动发卡片")
        try:
            card = feishu_api.build_task_card(
                "测试任务：客户 138****1111 需要回访",
                "test-guid-placeholder",
                "这是一条卡片测试，暂未创建真实任务。",
            )
            result = feishu_api.send_card(open_id, card)
            print(f"✅ 卡片已发送 message_id = {result.get('message_id')}")
        except Exception as exc:
            print(f"❌ {exc}")
            return 1

    # 4) 建任务
    if args.only in ("all", "task"):
        step("4. 创建任务（需要 task:task:write）")
        due = int((datetime.now() + timedelta(days=1)).timestamp() * 1000)
        try:
            guid = feishu_api.create_task(
                "测试任务：外呼 Agent 建单",
                [open_id],
                description="验证飞书任务创建链路是否打通，可手动删除。",
                due_ms=due,
            )
            print(f"✅ 任务 guid = {guid}")
        except Exception as exc:
            print(f"❌ {exc}")
            return 1

    print("\n全部完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
