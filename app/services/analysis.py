"""AI 客户分析编排：抢占 lead_brief → 子 Agent 分析 → 校验入库 → 裁决派单。"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta

from app.agents.customer_agent import run_customer_agent
from app.clients import dingtalk_api, feishu_api
from app.core.config import CHANNEL
from app.repositories import analysis_repo, call_record_repo, dispatch_repo, lead_brief_repo
from app.services import judge

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

    ai_level = str(analysis.get("intent_level") or "").strip().upper()
    if ai_level not in judge.LOCAL_LEVELS:
        return _fail(
            lead_id,
            record_id,
            retry_count,
            f"分析结果意向等级非法：{analysis.get('intent_level')!r}",
        )

    # 先裁决派单：失败则按重试策略重跑，避免撤销动作静默丢失
    try:
        _decide(record, analysis)
    except Exception as exc:  # noqa: BLE001
        return _fail(lead_id, record_id, retry_count, f"派单裁决失败：{exc}")

    lead_brief_repo.mark_done(lead_id, int(analysis["id"]))
    log.info(
        "分析完成 lead=%s record=%s analysis=%s version=%s",
        lead_id,
        record_id,
        analysis["id"],
        analysis.get("version"),
    )
    return True


def _decide(record: dict, analysis: dict) -> None:
    """按 AI 结果裁决派单：撤销不该触达的通知，补派供应商漏掉的高意向。"""
    record_id = str(record.get("record_id") or "")
    notice = dispatch_repo.get_notice_by_record(record_id)
    decision, consistent = judge.final_decision(record, analysis, has_notice=bool(notice))
    try:
        analysis_repo.set_level_consistent(int(analysis["id"]), consistent)
    except Exception:  # noqa: BLE001
        log.exception("写入 level_consistent 失败 record=%s", record_id)

    if decision == judge.REVOKE:
        reason = str(analysis.get("block_reason") or analysis.get("intent_level") or "")
        old_status = dispatch_repo.cancel_notice(record_id, f"AI 判定撤销：{reason}")
        log.info("AI 撤销派单 record=%s 原状态=%s 原因=%s", record_id, old_status, reason)
        if notice and old_status in ("SENT", "SENDING"):
            _notify_revoke(notice, record, analysis)
    elif decision == judge.PROMOTE:
        notice_id = dispatch_repo.enqueue_ai_notice(record, channel=CHANNEL)
        if notice_id:
            log.info("AI 补派单 record=%s notice=%s", record_id, notice_id)
        else:
            log.warning("AI 补派单未完成 record=%s（信用代码/销售名单缺失或已有通知）", record_id)
    else:
        dispatch_repo.mark_analysis_pending(record_id)


def _notify_revoke(notice: dict, record: dict, analysis: dict) -> None:
    """通知已发出后 AI 才否决时，补一条撤回提醒，避免销售继续回拨。"""
    user_id = str(notice.get("assignee_open_id") or "")
    if not user_id:
        return
    name = (
        str(record.get("customer_name") or "").strip()
        or str(record.get("customer_phone") or "").strip()
        or "该客户"
    )
    level = judge.INTENT_LABELS.get(
        str(analysis.get("intent_level") or "").upper(), "-"
    )
    reason = str(analysis.get("block_reason") or analysis.get("reason") or "-")
    text = (
        f"【AI 撤回派单】{name}\n"
        f"AI 复判：{level}（{reason}）\n"
        f"本次跟进通知已撤销，请勿继续回拨。"
    )
    channel = str(notice.get("channel") or "feishu").strip().lower()
    try:
        if channel == "dingtalk":
            dingtalk_api.send_bot_message([user_id], text)
        else:
            digest = hashlib.sha1(
                f"revoke-{record.get('record_id')}".encode("utf-8")
            ).hexdigest()
            feishu_api.send_text(user_id, text, dedup_key=f"revoke-{digest}")
    except Exception:  # noqa: BLE001
        log.exception("发送撤回提醒失败 record=%s", record.get("record_id"))


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
