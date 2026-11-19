"""常驻分析器：抢占 lead_brief → 客户分析子 Agent → 写结果并流转状态。

用法：
    python scripts/run_analyzer.py --once
    python scripts/run_analyzer.py --interval 10 --batch 3
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import socket
import sys
import time

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.repositories import lead_brief_repo  # noqa: E402
from app.services import analysis  # noqa: E402

log = logging.getLogger("run_analyzer")


def setup_logging() -> None:
    log_dir = PROJECT_ROOT / "state" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "analyzer.log", encoding="utf-8"),
        ],
    )


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def run_once(batch: int, max_retry: int) -> int:
    tasks = lead_brief_repo.claim_pending(batch, worker_id())
    if not tasks:
        return 0
    log.info("本轮抢占分析任务 %s 条", len(tasks))
    done = 0
    for task in tasks:
        if analysis.analyze_task(task):
            done += 1
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 客户分析 Worker")
    parser.add_argument("--interval", type=int, default=10, help="扫描间隔（秒）")
    parser.add_argument("--batch", type=int, default=3, help="每轮最多处理条数")
    parser.add_argument("--max-retry", type=int, default=analysis.MAX_RETRY)
    parser.add_argument("--lock-minutes", type=int, default=10, help="执行超时回收时间")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    args = parser.parse_args()

    setup_logging()
    reclaimed = lead_brief_repo.reclaim_timeout(args.lock_minutes, args.max_retry)
    if reclaimed:
        log.info("回收超时任务 %s 条", reclaimed)

    while True:
        try:
            done = run_once(max(1, int(args.batch or 3)), max(1, int(args.max_retry or 3)))
            if done:
                log.info("本轮完成分析 %s 条", done)
        except Exception:  # noqa: BLE001
            log.exception("分析轮次异常")

        if args.once:
            break
        time.sleep(max(1, int(args.interval or 10)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
