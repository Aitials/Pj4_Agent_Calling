"""离线自检：按渠道裁剪 Agent 工具集（不依赖 LLM / 数据库）。

覆盖：
1. 飞书渠道暴露全部工具
2. 企微 / 钉钉渠道不暴露飞书专属工具
3. 任何渠道都保留通用查询工具

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_tools_routing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.tools import ALL_TOOLS, FEISHU_ONLY, tools_for  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


def _names(tools: list) -> set[str]:
    return {getattr(tool, "name", "") for tool in tools}


def test_feishu() -> None:
    tools = tools_for("feishu")
    _check("飞书暴露全部工具", len(tools) == len(ALL_TOOLS))
    _check("飞书包含任务状态卡片工具", "query_aicc_task_status" in _names(tools))


def test_other_channels() -> None:
    for channel in ("wecom", "dingtalk"):
        tools = tools_for(channel)
        names = _names(tools)
        _check(f"{channel} 不暴露飞书专属工具", not (names & _names(FEISHU_ONLY)))
        _check(
            f"{channel} 保留通用查询工具",
            {"query_customer_profile", "query_aicc_dashboard"} <= names,
        )
        _check(f"{channel} 工具数量正确", len(tools) == len(ALL_TOOLS) - len(FEISHU_ONLY))


def test_unknown_channel() -> None:
    # 未指定渠道时按全量处理，避免把 CLI / 自检跑成残缺工具集
    _check("空渠道返回全部工具", len(tools_for("")) == len(ALL_TOOLS))


def main() -> int:
    test_feishu()
    test_other_channels()
    test_unknown_channel()
    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
