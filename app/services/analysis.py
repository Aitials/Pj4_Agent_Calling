"""AI 客户分析编排：抢占 lead_brief → 子 Agent 分析 → 校验入库 → 状态流转。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.agents.customer_agent import run_customer_agent
from app.repositories import analysis_repo, call_record_repo, dispatch_repo, lead_brief_repo

log = logging.getLogger("analysis")

MAX_RETRY = 3
BACKOFF_SECONDS = (10, 30, 60)


def analyze_task(task: dict) -> bool:
    """执行一条 lead_brief，成功返回 True。"""
    lead_id = int(task["id"])
    record_id = str(task.get("record_id") or "")
    retry_count = int(task.get("retry_count") or 0)

    record = call_record_repo.get_call_record(record_id)
    if not record:
        log.warning("分析任务 %s 对应的通话 %s 不存在，标记失败", lead_id, record_id)
        lead_brief_repo.mark_retry(
            lead_id, "通话记录不存在", MAX_RETRY, None, max_retry=MAX_RETRY
        )
        return False

    if not record.get("asr_transcript"):
        log.warning("通话 %s 无 ASR 转写，跳过分析", record_id)
        lead_brief_repo.mark_retry(
            lead_id, "无 ASR 转写，无法分析", MAX_RETRY, None, max_retry=MAX_RETRY
        )
        return False

    try:
        result = run_customer_agent({**record, "lead_brief_id": lead_id})
        log.info("子 Agent 返回 lead=%s：%s", lead_id, (result.get("text") or "")[:200])
    except Exception as exc:  # noqa: BLE001
        return _fail(lead_id, record_id, retry_count, f"子 Agent 执行失败：{exc}")

    analysis = analysis_repo.get_current(record_id)
    if not analysis or int(analysis.get("lead_brief_id") or 0) != lead_id:
        return _fail(lead_id, record_id, retry_count, "分析结果未真实入库")
    if not analysis.get("intent_level"):
        return _fail(lead_id, record_id, retry_count, "分析结果缺少意向等级")

    lead_brief_repo.mark_done(lead_id, int(analysis["id"]))
    dispatch_repo.mark_analysis_pending(record_id)
    log.info(
        "分析完成 lead=%s record=%s analysis=%s version=%s",
        lead_id,
        record_id,
        analysis["id"],
        analysis.get("version"),
    )
    return True


def _fail(lead_id: int, record_id: str, retry_count: int, message: str) -> bool:
    next_retry_count = retry_count + 1
    if next_retry_count < MAX_RETRY:
        delay = BACKOFF_SECONDS[min(next_retry_count - 1, len(BACKOFF_SECONDS) - 1)]
        next_retry_at = datetime.now() + timedelta(seconds=delay)
    else:
        next_retry_at = None
    log.warning(
        "分析失败 lead=%s record=%s retry=%s next=%s：%s",
        lead_id,
        record_id,
        next_retry_count,
        next_retry_at,
        message,
    )
    lead_brief_repo.mark_retry(
        lead_id,
        message,
        next_retry_count,
        next_retry_at,
        max_retry=MAX_RETRY,
    )
    return False
