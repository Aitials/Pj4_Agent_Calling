"""统一入库服务：通话记录与高意向双任务在同一事务内落库。

轮询（run_sync / sync_call_records）与实时回调（app.callback.service）共用本模块，
保证「call_record + dispatch_notice + lead_brief」原子写入。
"""

from __future__ import annotations

import logging
from datetime import date

from app.repositories import call_record_repo, customer_repo, dispatch_repo, lead_brief_repo
from app.services import intent

log = logging.getLogger("ingest")


def persist_record(
    record: dict,
    *,
    notify: bool = True,
    with_profile: bool = True,
) -> dict:
    """写入单条通话记录；命中高意向时同时创建通知与分析任务。

    返回 {"saved": bool, "notice_id": int|None}。
    """
    record_id = str(record.get("record_id") or "")
    if not record_id or not record.get("customer_phone"):
        log.warning("跳过缺少 record_id / 号码的记录：%s", record_id or record)
        return {"saved": False, "notice_id": None}

    credit_code = record.get("credit_code")
    assignee = None
    trigger_source = "SUPPLIER"

    if notify:
        should_notify, _rule = intent.evaluate(record)
        if should_notify and credit_code:
            # 已入队过就不再轮转销售，避免同一通话分配到不同人
            if dispatch_repo.get_notice_by_record(record_id):
                log.info("通话 %s 已存在通知记录，跳过派单", record_id)
            else:
                assignee = dispatch_repo.next_assignee()
                if not assignee:
                    log.warning("销售名单为空，通话 %s 无法派单", record_id)

    conn = customer_repo.connect()
    notice_id: int | None = None
    try:
        conn.begin()
        call_record_repo.upsert_call_record(record, conn=conn)

        if assignee:
            notice = {
                "credit_code": credit_code,
                "company_name": customer_repo.get_company_name(credit_code),
                "trigger_record_id": record_id,
                "trigger_phone": record.get("customer_phone"),
                "assignee_open_id": assignee["open_id"],
                "assignee_name": assignee.get("name"),
                "trigger_source": trigger_source,
                "notice_date": date.today(),
            }
            notice_id = dispatch_repo.enqueue_notice(notice, conn=conn)
            if notice_id:
                lead_brief_repo.enqueue(
                    record_id,
                    credit_code,
                    record.get("intent_level_code"),
                    conn=conn,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        log.exception("入库失败 record_id=%s", record_id)
        raise
    finally:
        conn.close()

    if with_profile and credit_code:
        try:
            customer_repo.refresh_call_aggregates(credit_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("刷新企业档案聚合失败 credit=%s：%s", credit_code, exc)

    if notice_id:
        log.info(
            "高意向入库并派单 record=%s intent=%s/%s assignee=%s notice_id=%s",
            record_id,
            record.get("intent_level_tag_id"),
            record.get("intent_level_code"),
            assignee.get("name") or assignee.get("open_id") if assignee else "-",
            notice_id,
        )
    return {"saved": True, "notice_id": notice_id}


def persist_records(
    records: list[dict],
    *,
    notify: bool = True,
    with_profile: bool = True,
) -> list[dict]:
    """批量写入，返回每条记录的入库结果。"""
    results: list[dict] = []
    for record in records:
        results.append(
            persist_record(record, notify=notify, with_profile=with_profile)
        )
    return results
