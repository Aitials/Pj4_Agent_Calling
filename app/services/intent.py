"""高意向判定：按一知标签组 + code 查本地规则。"""

from __future__ import annotations

import logging

from app.repositories import intent_rule_repo

log = logging.getLogger("intent_service")

# 派单硬阻断：空号/停机/分机错误、拒接
BLOCK_STATUSES = {"INVALID", "REJECTED"}
# 号码级/企业级黑名单标记（customer_contact.block_reason 取值）
BLOCK_REASONS = {"EMPTY_NUMBER", "REJECTED", "NOT_OWNER", "DO_NOT_DISTURB"}


def evaluate(record: dict) -> tuple[bool, dict | None]:
    """返回 (是否通知, 命中的规则)。未配置或无规则时按不通知处理。"""
    tag_id = record.get("intent_level_tag_id")
    code = record.get("intent_level_code")
    rule = intent_rule_repo.get_rule(tag_id, code)
    if not rule:
        return False, None
    return bool(rule.get("notify")), rule


def check_block(record: dict, contact: dict | None) -> str | None:
    """派单前硬闸门：命中阻断返回原因代码，None 表示放行。

    阻断依据（任一命中即阻断）：
    1. 通话状态为空号/停机/分机错误（INVALID）或拒接（REJECTED）；
    2. 号码或所属企业已在黑名单；
    3. 号码上已标记勿扰/非本人/空号等原因。
    """
    status = str(record.get("call_status") or "").strip().upper()
    if status in BLOCK_STATUSES:
        return status
    if contact:
        if int(contact.get("is_blacklist") or 0) or int(contact.get("profile_blacklist") or 0):
            return "BLACKLIST"
        reason = str(contact.get("block_reason") or "").strip().upper()
        if reason in BLOCK_REASONS:
            return reason
    return None


def contact_block_reason(code: str) -> str | None:
    """把阻断代码映射为 customer_contact.block_reason；无需回写时返回 None。"""
    return {"INVALID": "EMPTY_NUMBER", "REJECTED": "REJECTED"}.get(code)
