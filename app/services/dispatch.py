"""高意向派单：轮转选人并写入 dispatch_notice。"""

from __future__ import annotations

import logging
from datetime import date

from app.repositories import customer_repo, dispatch_repo
from app.services import intent

log = logging.getLogger("dispatch_service")


def enqueue_record(record: dict) -> dict | None:
    """命中高意向则入队；未命中、缺企业、已入队或无人可派时返回 None。"""
    record_id = str(record.get("record_id") or "")
    should_notify, rule = intent.evaluate(record)
    if not should_notify:
        return None

    if dispatch_repo.get_notice_by_record(record_id):
        return None

    credit_code = record.get("credit_code")
    if not credit_code:
        log.warning("通话 %s 命中高意向但缺少 credit_code，跳过通知", record_id)
        return None

    assignee = dispatch_repo.next_assignee()
    if not assignee:
        log.warning("销售名单为空，通话 %s 无法派单", record_id)
        return None

    notice = {
        "credit_code": credit_code,
        "company_name": customer_repo.get_company_name(credit_code),
        "trigger_record_id": record_id,
        "trigger_phone": record.get("customer_phone"),
        "assignee_open_id": assignee["open_id"],
        "assignee_name": assignee.get("name"),
        "trigger_source": "SUPPLIER",
        "notice_date": date.today(),
    }
    notice_id = dispatch_repo.enqueue_notice(notice)
    log.info(
        "高意向入队 record=%s intent=%s/%s assignee=%s notice_id=%s",
        record_id,
        record.get("intent_level_tag_id"),
        record.get("intent_level_code"),
        assignee.get("name") or assignee.get("open_id"),
        notice_id,
    )
    return {"notice_id": notice_id, **notice}


def enqueue_records(records: list[dict]) -> list[dict]:
    created: list[dict] = []
    for record in records:
        try:
            notice = enqueue_record(record)
        except Exception as exc:  # noqa: BLE001
            log.exception("通话 %s 入队失败：%s", record.get("record_id"), exc)
            continue
        if notice:
            created.append(notice)
    return created
