"""离线自检：分析结果校验与派单裁决分支（不依赖 LLM / 数据库）。

覆盖：
1. 意向等级归一与 Literal 枚举校验
2. blocked 严格布尔、字段长度/条数校验
3. quote 是否逐字出现在 ASR 转写中（忽略空白）
4. judge.final_decision 在非法等级、blocked 非 0/1 时的兜底分支

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_analysis_validation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.agents import customer_agent as ca  # noqa: E402
from app.services import judge  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


def _payload(**overrides) -> ca._SavePayload:
    data = {
        "intent_level": "HIGH",
        "reason": "客户主动询价",
        "quote": "多少钱一年",
        "customer_needs": ["报价"],
        "concerns": ["价格"],
        "objections": [],
        "intent_evidence": ["客户主动询价"],
        "opening": "您好，针对您刚提到的报价需求，这里给您一个具体方案。",
        "value_points": ["针对价格：按年付费更划算", "针对需求：可先试用", "针对顾虑：支持随时停用"],
        "objection_handling": [],
        "next_action": "次日 10:00 前电话回访",
    }
    data.update(overrides)
    return ca._SavePayload(**data)


def _rejected(**overrides) -> bool:
    try:
        _payload(**overrides)
    except ValidationError:
        return True
    return False


def test_levels() -> None:
    _check("等级归一：' 高意向 ' → HIGH", ca._normalize_level(" 高意向 ") == "HIGH")
    _check("等级归一：'medium' → MEDIUM", ca._normalize_level("medium") == "MEDIUM")
    _check("未知等级原样保留", ca._normalize_level("VERY_HIGH") == "VERY_HIGH")
    _check("非法等级被 Literal 拒绝", _rejected(intent_level="VERY_HIGH"))
    _check("中文等级无法通过模型校验", _rejected(intent_level="高意向"))


def test_blocked_and_lengths() -> None:
    _check("blocked=True 可接受", _payload(blocked=True).blocked is True)
    _check("blocked=2 被拒绝", _rejected(blocked=2))
    _check("blocked='maybe' 被拒绝", _rejected(blocked="maybe"))
    _check("空 opening 被拒绝", _rejected(opening=""))
    _check("value_points 少于 3 条被拒绝", _rejected(value_points=["只有一条"]))
    _check("reason 超 512 字被拒绝", _rejected(reason="长" * 513))
    _check("customer_needs 超过 5 条被拒绝", _rejected(customer_needs=[f"需求{i}" for i in range(6)]))


def test_quote() -> None:
    transcript = "客户：你们这个 系统\n多少钱一年？销售：按年付费。"
    _check("quote 忽略空白可命中", ca._quote_in_transcript("多少钱一年", transcript))
    _check("改写后的 quote 不命中", not ca._quote_in_transcript("价格太贵了", transcript))
    _check("空 quote 不命中", not ca._quote_in_transcript("  ", transcript))
    _check("机器人台词不能作为客户原话", not ca._quote_in_transcript("按年付费", transcript))
    _check("客户多句仍可跨行命中", ca._quote_in_transcript("你们这个系统多少钱一年", transcript))
    unlabeled = "你们这个系统多少钱一年？按年付费。"
    _check("无说话人标签退回全文匹配", ca._quote_in_transcript("按年付费", unlabeled))
    only_bot = "AI：您好，请问需要报价吗？客户：不需要。"
    _check("只引用机器人台词被拒绝", not ca._quote_in_transcript("请问需要报价吗", only_bot))
    _check("客户发言可命中", ca._quote_in_transcript("不需要", only_bot))


def test_judge() -> None:
    original = judge.intent_rule_repo.get_rule
    judge.intent_rule_repo.get_rule = lambda *a, **k: None  # 自检不查库
    try:
        promote, _ = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "HIGH", "blocked": 0}, has_notice=False
        )
        _check("HIGH 且无通知 → PROMOTE", promote == judge.PROMOTE)

        unknown_keep, consistent = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "VERY_HIGH", "blocked": 0}, has_notice=True
        )
        _check("非法等级 + 有通知 → KEEP", unknown_keep == judge.KEEP)
        _check("非法等级不参与一致性统计", consistent is None)

        unknown_none, _ = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "VERY_HIGH", "blocked": 0}, has_notice=False
        )
        _check("非法等级 + 无通知 → NONE", unknown_none == judge.NONE)

        revoke, _ = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "HIGH", "blocked": True}, has_notice=True
        )
        _check("blocked=True → REVOKE", revoke == judge.REVOKE)

        revoke_odd, _ = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "HIGH", "blocked": 2}, has_notice=True
        )
        _check("blocked=2 视为阻断 → REVOKE", revoke_odd == judge.REVOKE)

        low_none, _ = judge.final_decision(
            {"record_id": "r1"}, {"intent_level": "LOW", "blocked": 0}, has_notice=False
        )
        _check("LOW 且无通知 → NONE", low_none == judge.NONE)
    finally:
        judge.intent_rule_repo.get_rule = original


def main() -> int:
    test_levels()
    test_blocked_and_lengths()
    test_quote()
    test_judge()

    if _failures:
        print(f"\n=== 失败 {len(_failures)} 项：{', '.join(_failures)} ===")
        return 1
    print("\n=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
