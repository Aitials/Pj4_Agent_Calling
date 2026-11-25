"""钉钉渠道 · 阶段 3 冒烟（身份与 unionId）。

阶段 3 只做两件事：
1. 会话记忆按 `senderStaffId`（明文 userId）隔离 —— 由 run_dingtalk.py 实测；
2. userId → unionId 查询 —— 本脚本验证（unionId 只服务阶段 6 建待办）。

用法：
    python scripts/dingtalk_stage3_check.py check
    python scripts/dingtalk_stage3_check.py union-id --user-id <userId> [--show]

依赖：应用需开通「成员信息读权限 `qyapi_get_member`」（实测仅开
`Contact.User.Read` 会报 403 并返回 `requiredScopes=[qyapi_get_member]`）；
查询走 api.dingtalk.com，受服务端出口 IP 白名单约束。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.clients import dingtalk_api  # noqa: E402
from app.core.config import (  # noqa: E402
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
    DINGTALK_ROBOT_CODE,
)


def _mask(value: str) -> str:
    text = str(value or "")
    if len(text) <= 10:
        return text[:2] + "*" * max(len(text) - 2, 0)
    return f"{text[:6]}…{text[-4:]}"


def cmd_check() -> int:
    print("== 阶段 3 配置 ==")
    print(f"DINGTALK_CLIENT_ID     : {dingtalk_api.mask_secret(DINGTALK_CLIENT_ID) or '（空）'}")
    print(f"DINGTALK_CLIENT_SECRET : {dingtalk_api.mask_secret(DINGTALK_CLIENT_SECRET) or '（空）'}")
    print(f"DINGTALK_ROBOT_CODE    : {DINGTALK_ROBOT_CODE or '（空）'}")

    if not dingtalk_api.is_configured():
        print("\n缺少 Client ID / Secret，无法验证 unionId")
        return 1

    token = dingtalk_api.access_token()
    if not token:
        print("\naccessToken 获取失败：检查凭证与出口 IP 白名单")
        return 1

    print("\naccessToken 可用；执行 union-id 需要 Contact.User.Read 权限")
    return 0


def cmd_union(user_id: str, show: bool) -> int:
    start = time.perf_counter()
    union = dingtalk_api.union_id(user_id)
    first_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    again = dingtalk_api.union_id(user_id)
    second_ms = (time.perf_counter() - start) * 1000

    if not union:
        print(f"userId {user_id} 的 unionId 获取失败")
        print("排查：① 是否已开通 qyapi_get_member（成员信息读权限）")
        print("      ② 用户是否在本组织且在应用可见范围")
        print("      ③ 出口 IP 白名单是否包含本机公网 IP")
        return 1

    print(f"userId  : {user_id}")
    print(f"unionId : {union if show else _mask(union)}")
    print(f"耗时    : 首次 {first_ms:.0f} ms，第二次（缓存）{second_ms:.2f} ms")
    return 0 if again == union else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="钉钉渠道阶段 3 冒烟（unionId）")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="打印配置并验证 accessToken（默认）")

    union = sub.add_parser("union-id", help="userId → unionId 查询与缓存验证")
    union.add_argument("--user-id", required=True, help="钉钉明文 userId（senderStaffId）")
    union.add_argument("--show", action="store_true", help="打印完整 unionId")

    args = parser.parse_args()

    if args.command in (None, "check"):
        return cmd_check()
    if args.command == "union-id":
        return cmd_union(args.user_id, args.show)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
