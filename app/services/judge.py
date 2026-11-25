"""最终判定：供应商标签只作触发信号，AI 分析结果为准。

规则：
- AI 判定 blocked 或 LOW / INVALID → 撤销已派单（未派单则无需动作）；
- AI 判定 HIGH / MEDIUM 但供应商未触发派单 → 补派单（trigger_source=AI）；
  例外：供应商已判高意向但人工关闭了通知（local_level=HIGH 且 notify=0），尊重人工设置；
- 其余保留原派单；
- 同时计算 level_consistent（供应商本地分级与 AI 等级是否一致），不一致留档。
"""

from __future__ import annotations

import logging

from app.repositories import intent_rule_repo

log = logging.getLogger("judge")

LOCAL_LEVELS = {"HIGH", "MEDIUM", "LOW", "INVALID"}
PROMOTE_LEVELS = {"HIGH", "MEDIUM"}

INTENT_LABELS = {"HIGH": "高意向", "MEDIUM": "中意向", "LOW": "低意向", "INVALID": "无效"}

REVOKE = "REVOKE"
KEEP = "KEEP"
PROMOTE = "PROMOTE"
NONE = "NONE"


def supplier_local_level(record: dict) -> str | None:
    """供应商标签经 intent_level_rule 映射出的本地分级；无规则返回 None。"""
    rule = intent_rule_repo.get_rule(
        record.get("intent_level_tag_id"), record.get("intent_level_code")
    )
    level = str((rule or {}).get("local_level") or "").strip().upper()
    return level if level in LOCAL_LEVELS else None


def final_decision(record: dict, analysis: dict, *, has_notice: bool) -> tuple[str, int | None]:
    """返回 (裁决, level_consistent)。

    裁决：REVOKE 撤销派单 / PROMOTE 补派单 / KEEP 保留原派单 / NONE 无需动作。
    """
    ai_level = str(analysis.get("intent_level") or "").strip().upper()
    blocked = bool(analysis.get("blocked"))
    rule = intent_rule_repo.get_rule(
        record.get("intent_level_tag_id"), record.get("intent_level_code")
    )
    supplier_level = str((rule or {}).get("local_level") or "").strip().upper()
    if supplier_level not in LOCAL_LEVELS:
        supplier_level = None
    consistent = None if supplier_level is None else int(supplier_level == ai_level)

    # 阻断优先判断：blocked 为真或低意向直接撤销，避免异常等级造成漏撤
    if blocked or ai_level in {"LOW", "INVALID"}:
        return (REVOKE if has_notice else NONE), consistent
    if ai_level not in LOCAL_LEVELS:
        # 未知等级不猜：保留既有派单、不新增派单，并留告警便于排查上游
        log.warning(
            "AI 意向等级非法，按不动作处理 record=%s level=%r",
            record.get("record_id"),
            analysis.get("intent_level"),
        )
        return (KEEP if has_notice else NONE), None
    if ai_level in PROMOTE_LEVELS:
        if has_notice:
            return KEEP, consistent
        # 供应商已判高意向却关闭了通知：属于人工设置，AI 不覆盖
        if supplier_level == "HIGH" and not int((rule or {}).get("notify") or 0):
            return NONE, consistent
        return PROMOTE, consistent
    return (KEEP if has_notice else NONE), consistent
