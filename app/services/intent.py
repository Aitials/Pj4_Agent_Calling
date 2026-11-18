"""高意向判定：按一知标签组 + code 查本地规则。"""

from __future__ import annotations

import logging

from app.repositories import intent_rule_repo

log = logging.getLogger("intent_service")


def evaluate(record: dict) -> tuple[bool, dict | None]:
    """返回 (是否通知, 命中的规则)。未配置或无规则时按不通知处理。"""
    tag_id = record.get("intent_level_tag_id")
    code = record.get("intent_level_code")
    rule = intent_rule_repo.get_rule(tag_id, code)
    if not rule:
        return False, None
    return bool(rule.get("notify")), rule
