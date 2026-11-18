"""拉取一知通话记录写入 call_record（不落文件）。

用法：
    # 手动补某任务历史
    python scripts/sync_call_records.py --job 1704345203463 --start "2026-07-01 00:00:00" --end "2026-11-19 00:00:00"

    # 某任务近 7 天
    python scripts/sync_call_records.py --job 1704345203463 --days 7

    # 自动发现近 90 天任务
    python scripts/sync_call_records.py --all --days 90

    # 只读演练
    python scripts/sync_call_records.py --job 1704345203463 --days 7 --dry-run
"""

from __future__ import annotations

import argparse
import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services import call_record_sync  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取一知通话记录并入库")
    parser.add_argument("--job", type=int, action="append", default=[], help="任务 ID，可重复")
    parser.add_argument("--all", action="store_true", help="自动发现近 N 天任务")
    parser.add_argument("--start", default=None, help="开始时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end", default=None, help="结束时间 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--days", type=int, default=7, help="未给 start 时回溯天数")
    parser.add_argument("--sleep", type=float, default=2.5, help="详情请求间隔（秒）")
    parser.add_argument("--limit", type=int, default=0, help="每个任务最多处理条数，0 为不限")
    parser.add_argument("--dry-run", action="store_true", help="只拉取和解析，不写库")
    parser.add_argument("--no-profile", action="store_true", help="不更新 customer_profile 通话聚合")
    args = parser.parse_args()

    if not args.job and not args.all:
        parser.error("至少指定 --job 或 --all")

    jobs = list(args.job)
    if args.all:
        jobs = call_record_sync.discover_jobs(days=args.days, explicit_jobs=jobs)
    if not jobs:
        print("没有可同步的任务")
        return 0

    print(f"待同步任务：{', '.join(str(job) for job in jobs)}")
    summaries = call_record_sync.sync_jobs(
        jobs,
        days=args.days,
        start=args.start,
        end=args.end,
        sleep=args.sleep,
        limit=args.limit,
        dry_run=args.dry_run,
        with_profile=not args.no_profile,
    )

    failed = 0
    for summary in summaries:
        status = summary.get("status")
        print(
            f"\n任务 {summary['job_id']}：{status}，"
            f"列表 {summary.get('fetched', 0)} 条，入库 {summary.get('saved', 0)} 条，"
            f"详情 {summary.get('detail_calls', 0)} 次"
        )
        if status == "FAILED":
            failed += 1
            print(f"  错误：{summary.get('error')}")
            continue
        if args.dry_run:
            for record in summary.get("records") or []:
                transcript = record.get("asr_transcript") or ""
                print(
                    f"  - {record.get('record_id')}  {record.get('call_status')}  "
                    f"意向 {record.get('intent_level_tag_id')}/{record.get('intent_level_code')}  "
                    f"转写 {len(transcript.splitlines())} 行"
                )

    return 1 if failed == len(summaries) else 0


if __name__ == "__main__":
    raise SystemExit(main())
