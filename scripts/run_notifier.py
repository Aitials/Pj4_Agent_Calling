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

from app.cards.high_intent_card import (  # noqa: E402
    build_high_intent_card,
    build_task_description,
    build_task_summary,
)
from app.clients import feishu_api  # noqa: E402
from app.repositories import call_record_repo, dispatch_repo  # noqa: E402

log = logging.getLogger("run_notifier")

BACKOFF_MINUTES = (1, 5, 25)


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
            delay = BACKOFF_MINUTES[min(retry_count - 1, len(BACKOFF_MINUTES) - 1)]
            next_retry_at = datetime.now() + timedelta(minutes=delay)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="发送高意向通知队列")
    parser.add_argument("--interval", type=int, default=5, help="扫描间隔（秒）")
    parser.add_argument("--batch", type=int, default=20, help="每轮最多发送条数")
    parser.add_argument("--max-retry", type=int, default=3, help="最大尝试次数")
    parser.add_argument("--resend", action="store_true", help="把 FAILED 重置为 PENDING 后重发")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    args = parser.parse_args()

    setup_logging()
    reclaimed = dispatch_repo.reclaim_sending()
    if reclaimed:
        log.info("回收 SENDING 超时记录 %s 条", reclaimed)
    if args.resend:
        reset = dispatch_repo.reset_failed()
        log.info("重置 FAILED 记录 %s 条", reset)

    while True:
        notices = dispatch_repo.claim_pending(args.batch)
        if notices:
            log.info("本轮抢占 %s 条通知", len(notices))
        for notice in notices:
            send_one(notice, max(1, int(args.max_retry or 3)))

        if args.once:
            break
        time.sleep(max(1, int(args.interval or 5)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
