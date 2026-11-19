"""常驻通知发送器：扫 PENDING 队列 → 飞书任务 + 卡片 → 状态流转。

用法：
    python scripts/run_notifier.py --once
    python scripts/run_notifier.py --interval 5
    python scripts/run_notifier.py --resend --once
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import sys
import time
from datetime import datetime, timedelta

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.cards.analysis_card import build_analysis_card  # noqa: E402
from app.cards.high_intent_card import (  # noqa: E402
    build_high_intent_card,
    build_task_description,
    build_task_summary,
)
from app.clients import feishu_api  # noqa: E402
from app.repositories import (  # noqa: E402
    analysis_repo,
    call_record_repo,
    customer_repo,
    dispatch_repo,
)
from app.services import brief  # noqa: E402

log = logging.getLogger("run_notifier")

BACKOFF_SECONDS = (10, 30, 60)


def setup_logging() -> None:
    log_dir = PROJECT_ROOT / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "notify.log", encoding="utf-8"),
        ],
    )


def _dedup_key(record_id: str, open_id: str) -> str:
    digest = hashlib.sha1(f"{record_id}-{open_id}".encode("utf-8")).hexdigest()
    return f"notice-{digest}"


def send_one(notice: dict, max_retry: int) -> bool:
    notice_id = int(notice["id"])
    record_id = str(notice.get("trigger_record_id") or "")
    open_id = str(notice.get("assignee_open_id") or "")
    task_guid = notice.get("feishu_task_guid")
    dedup_key = _dedup_key(record_id, open_id)

    try:
        record = call_record_repo.get_call_record(record_id)
        if not record:
            raise RuntimeError(f"通话记录 {record_id} 不存在")

        # 企业名以通知队列为准，通话表不一定有
        view = {
            **record,
            "company_name": notice.get("company_name"),
            "trigger_phone": notice.get("trigger_phone"),
        }

        if not task_guid:
            task_guid = feishu_api.create_task(
                build_task_summary(view),
                [open_id],
                description=build_task_description(view),
                client_token=dedup_key,
            )
            dispatch_repo.set_task_guid(notice_id, task_guid)

        card = build_high_intent_card(view, notice, task_guid)
        result = feishu_api.send_card(open_id, card, dedup_key=dedup_key)
        dispatch_repo.mark_sent(notice_id, task_guid, result.get("message_id"))
        log.info(
            "通知已发送 notice=%s record=%s assignee=%s task=%s",
            notice_id,
            record_id,
            notice.get("assignee_name") or open_id,
            task_guid,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        retry_count = int(notice.get("retry_count") or 0) + 1
        next_retry_at = None
        if retry_count < max_retry:
            delay = BACKOFF_SECONDS[min(retry_count - 1, len(BACKOFF_SECONDS) - 1)]
            next_retry_at = datetime.now() + timedelta(seconds=delay)
        try:
            dispatch_repo.mark_failed(
                notice_id,
                str(exc),
                retry_count,
                next_retry_at,
                task_guid=task_guid,
            )
        except Exception:  # noqa: BLE001
            log.exception("标记通知失败时又失败 notice=%s", notice_id)
        log.warning(
            "通知发送失败 notice=%s record=%s retry=%s next=%s：%s",
            notice_id,
            record_id,
            retry_count,
            next_retry_at,
            exc,
        )
        return False


def send_analysis_one(notice: dict, max_retry: int) -> bool:
    """发送分析结果通知：优先原地更新基础卡片，失败或消息不存在则补发。"""
    notice_id = int(notice["id"])
    record_id = str(notice.get("trigger_record_id") or "")
    try:
        record = call_record_repo.get_call_record(record_id)
        if not record:
            raise RuntimeError(f"通话记录 {record_id} 不存在")
        analysis = analysis_repo.get_current(record_id)
        if not analysis:
            raise RuntimeError(f"通话 {record_id} 暂无分析结果")

        view = {
            **record,
            "company_name": notice.get("company_name"),
            "trigger_phone": notice.get("trigger_phone"),
        }
        card = build_analysis_card(view, analysis, notice)

        message_id = notice.get("card_message_id")
        new_message_id = None
        if message_id:
            try:
                feishu_api.update_card(message_id, card)
            except Exception as exc:  # noqa: BLE001
                log.warning("原卡片更新失败，改为补发新卡片 notice=%s：%s", notice_id, exc)
                result = feishu_api.send_card(notice["assignee_open_id"], card)
                new_message_id = result.get("message_id")
        else:
            result = feishu_api.send_card(notice["assignee_open_id"], card)
            new_message_id = result.get("message_id")

        dispatch_repo.mark_analysis_sent(notice_id, new_message_id)
        if notice.get("feishu_task_guid"):
            dispatch_repo.mark_attachment_pending(notice_id)
        else:
            dispatch_repo.mark_attachment_skipped(notice_id, "未创建飞书任务，跳过附件")
        log.info(
            "分析结果已通知 notice=%s record=%s analysis=%s",
            notice_id,
            record_id,
            analysis.get("id"),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        retry_count = int(notice.get("analysis_retry_count") or 0) + 1
        try:
            dispatch_repo.mark_analysis_failed(notice_id, str(exc), retry_count, max_retry)
        except Exception:  # noqa: BLE001
            log.exception("标记分析通知失败时又失败 notice=%s", notice_id)
        log.warning(
            "分析通知失败 notice=%s record=%s retry=%s：%s",
            notice_id,
            record_id,
            retry_count,
            exc,
        )
        return False


def attach_brief_one(notice: dict, max_retry: int) -> bool:
    """生成简报 → 本地留档 → 上传附件 → 更新任务描述。

    附件失败只影响 attachment_status，不影响已发送的卡片。
    """
    notice_id = int(notice["id"])
    record_id = str(notice.get("trigger_record_id") or "")
    task_guid = str(notice.get("feishu_task_guid") or "").strip()
    try:
        if not task_guid:
            raise RuntimeError("飞书任务不存在，无法上传附件")
        record = call_record_repo.get_call_record(record_id)
        if not record:
            raise RuntimeError(f"通话记录 {record_id} 不存在")
        analysis = analysis_repo.get_current(record_id)
        if not analysis:
            raise RuntimeError(f"通话 {record_id} 暂无分析结果")

        profile = customer_repo.get_profile(str(record.get("credit_code") or ""))
        content = brief.build_markdown(record, analysis, profile)
        paths = brief.save_local(
            record_id,
            int(analysis.get("version") or 1),
            content,
            brief.company_of(record, profile),
        )

        guid = notice.get("attachment_guid")
        if guid:
            log.info("附件已存在，跳过上传 notice=%s guid=%s", notice_id, guid)
        else:
            result = feishu_api.upload_task_attachment(
                task_guid, paths["filename"], content.encode("utf-8")
            )
            guid = result.get("guid")
            if not guid:
                raise RuntimeError("上传附件成功但缺少 GUID")
            dispatch_repo.set_attachment_guid(
                notice_id, guid, result.get("url"), paths["version_path"]
            )

        description = brief.build_task_description_with_attachment(
            record, paths["filename"]
        )
        feishu_api.update_task_description(task_guid, description)
        dispatch_repo.mark_attachment_done(notice_id)
        log.info(
            "简报附件完成 notice=%s record=%s guid=%s path=%s",
            notice_id,
            record_id,
            guid,
            paths["version_path"],
        )
        return True
    except Exception as exc:  # noqa: BLE001
        retry_count = int(notice.get("attachment_retry_count") or 0) + 1
        try:
            dispatch_repo.mark_attachment_failed(notice_id, str(exc), retry_count, max_retry)
        except Exception:  # noqa: BLE001
            log.exception("标记附件失败时又失败 notice=%s", notice_id)
        log.warning(
            "简报附件失败 notice=%s record=%s retry=%s：%s",
            notice_id,
            record_id,
            retry_count,
            exc,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="发送高意向通知队列")
    parser.add_argument("--interval", type=int, default=5, help="扫描间隔（秒）")
    parser.add_argument("--batch", type=int, default=20, help="每轮最多发送条数")
    parser.add_argument("--max-retry", type=int, default=3, help="最大尝试次数")
    parser.add_argument("--resend", action="store_true", help="把 FAILED 重置为 PENDING 后重发")
    parser.add_argument("--notice-id", type=int, default=0, help="只补跑指定通知的附件")
    parser.add_argument("--record-id", default="", help="只补跑指定通话的附件")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    args = parser.parse_args()

    setup_logging()
    reclaimed = dispatch_repo.reclaim_sending()
    if reclaimed:
        log.info("回收 SENDING 超时记录 %s 条", reclaimed)
    reclaimed_analysis = dispatch_repo.reclaim_analysis_sending()
    if reclaimed_analysis:
        log.info("回收分析通知 SENDING 超时记录 %s 条", reclaimed_analysis)
    reclaimed_attachment = dispatch_repo.reclaim_attachment_uploading()
    if reclaimed_attachment:
        log.info("回收附件 UPLOADING 超时记录 %s 条", reclaimed_attachment)
    if args.resend:
        reset = dispatch_repo.reset_failed()
        log.info("重置 FAILED 记录 %s 条", reset)

    if args.notice_id or args.record_id:
        notice = (
            dispatch_repo.get_notice(args.notice_id)
            if args.notice_id
            else dispatch_repo.get_notice_by_record(args.record_id)
        )
        if not notice:
            log.error("未找到指定通知 notice_id=%s record_id=%s", args.notice_id, args.record_id)
            return 1
        dispatch_repo.mark_attachment_pending(notice["id"])
        notice = dispatch_repo.get_notice(notice["id"]) or notice
        attach_brief_one(notice, max(1, int(args.max_retry or 3)))
        return 0

    while True:
        notices = dispatch_repo.claim_pending(args.batch)
        if notices:
            log.info("本轮抢占 %s 条通知", len(notices))
        for notice in notices:
            send_one(notice, max(1, int(args.max_retry or 3)))

        analysis_notices = dispatch_repo.claim_analysis_pending(
            args.batch, max(1, int(args.max_retry or 3))
        )
        if analysis_notices:
            log.info("本轮抢占 %s 条分析通知", len(analysis_notices))
        for notice in analysis_notices:
            send_analysis_one(notice, max(1, int(args.max_retry or 3)))

        attachment_notices = dispatch_repo.claim_attachment_pending(
            args.batch, max(1, int(args.max_retry or 3))
        )
        if attachment_notices:
            log.info("本轮抢占 %s 条附件任务", len(attachment_notices))
        for notice in attachment_notices:
            attach_brief_one(notice, max(1, int(args.max_retry or 3)))

        if args.once:
            break
        time.sleep(max(1, int(args.interval or 5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
