"""常驻同步：拉取通话记录 → 入库 → 高意向判定 → 写通知队列。

用法：
    python scripts/run_sync.py --once --job 1704345203463 --days 200 --job-only
    python scripts/run_sync.py --interval 60 --days 90
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import time

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories.customer_repo import connect  # noqa: E402
from app.services import call_record_sync  # noqa: E402

log = logging.getLogger("run_sync")


def setup_logging() -> None:
    log_dir = PROJECT_ROOT / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "sync.log", encoding="utf-8"),
        ],
    )


def acquire_lock():
    """MySQL 命名锁，避免两轮同步重叠。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK('aicc_sync', 0) AS locked")
            row = cur.fetchone() or {}
            if int(row.get("locked") or 0) == 1:
                return conn
        conn.close()
        return None
    except Exception:
        conn.close()
        raise


def release_lock(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK('aicc_sync')")
    finally:
        conn.close()


def run_once(args) -> tuple[int, int, int]:
    jobs = call_record_sync.discover_jobs(
        days=args.days,
        explicit_jobs=args.job,
    ) if not args.job_only else list(args.job)

    if not jobs:
        log.info("没有可同步的任务")
        return 0, 0, 0

    summaries = call_record_sync.sync_jobs(
        jobs,
        days=args.days,
        sleep=args.sleep,
        limit=args.limit,
        with_profile=not args.no_profile,
    )
    records = [record for summary in summaries for record in summary.get("records") or []]
    notified = sum(int(summary.get("notified") or 0) for summary in summaries)
    log.info(
        "同步轮次完成：任务 %s 个，记录 %s 条，派单 %s 条",
        len(summaries),
        len(records),
        notified,
    )
    return len(summaries), len(records), notified


def main() -> int:
    parser = argparse.ArgumentParser(description="常驻同步通话记录并按高意向入队")
    parser.add_argument("--interval", type=int, default=60, help="轮询间隔（秒）")
    parser.add_argument("--days", type=int, default=90, help="自动发现任务回溯天数")
    parser.add_argument("--job", type=int, action="append", default=[], help="指定任务 ID，可重复")
    parser.add_argument("--job-only", action="store_true", help="只同步 --job 指定的任务，不自动发现")
    parser.add_argument("--sleep", type=float, default=2.5, help="详情请求间隔（秒）")
    parser.add_argument("--limit", type=int, default=0, help="每个任务最多处理条数，0 为不限")
    parser.add_argument("--no-profile", action="store_true", help="不更新 customer_profile 聚合")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    args = parser.parse_args()

    setup_logging()
    if args.job_only and not args.job:
        parser.error("--job-only 需要同时指定 --job")

    while True:
        lock = acquire_lock()
        if lock is None:
            log.warning("上一轮同步仍在运行，跳过本轮")
        else:
            try:
                run_once(args)
            except Exception:  # noqa: BLE001
                log.exception("同步轮次异常")
            finally:
                release_lock(lock)

        if args.once:
            break
        time.sleep(max(5, int(args.interval or 60)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
