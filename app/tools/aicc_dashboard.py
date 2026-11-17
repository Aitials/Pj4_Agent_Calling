"""一知 AI 外呼首页看板查询工具。

对应页面：`/active-callout-center/homePage`
数据来源（4 组接口，默认查当天，可传日期区间）：
- /apiEngine/callStatsIndex/indexStatsInfo     核心 KPI
- /apiEngine/callStatsIndex/intentLevelPieChart 意向分布
- /apiEngine/callStatsIndex/callStatusLineChart 每日趋势
- /apiEngine/robotCount/robotInfo + indexJobInfo 机器人与运行任务
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from langchain_core.tools import tool

from app.clients import tanyibot
from app.core import cache

SYSTEM_TYPE = "CALL_OUT"
DATE_FMT = "%Y-%m-%d"


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_date(raw: str, default: date) -> date:
    raw = (raw or "").strip()
    if not raw:
        return default
    return datetime.strptime(raw, DATE_FMT).date()


def _load(key: str, loader):
    """每个接口独立缓存，命中即为旧数据，未命中会调用 loader 并回写。"""
    data, _hit = cache.get_or_load(key, loader)
    return data


def _fetch_stats(start: date, end: date) -> dict:
    return _load(
        f"aicc:dash:stats:{start}:{end}",
        lambda: tanyibot.get(
            "/apiEngine/callStatsIndex/indexStatsInfo",
            params={
                "statsDim": "STATS_CALL_JOB",
                "statsIds": "",
                "startDate": start.strftime(DATE_FMT),
                "endDate": end.strftime(DATE_FMT),
                "userId": "",
                "intentLevelTagId": 0,
                "systemType": SYSTEM_TYPE,
            },
        )
        or {},
    )


def _fetch_intent(start: date, end: date) -> dict:
    return _load(
        f"aicc:dash:intent:{start}:{end}",
        lambda: tanyibot.get(
            "/apiEngine/callStatsIndex/intentLevelPieChart",
            params={
                "intentLevelTagId": 0,
                "startDate": start.strftime(DATE_FMT),
                "endDate": end.strftime(DATE_FMT),
                "systemType": SYSTEM_TYPE,
            },
        )
        or {},
    )


def _fetch_trend(start: date, end: date) -> list[dict]:
    return _load(
        f"aicc:dash:trend:{start}:{end}",
        lambda: tanyibot.get(
            "/apiEngine/callStatsIndex/callStatusLineChart",
            params={
                "startDate": start.strftime(DATE_FMT),
                "endDate": end.strftime(DATE_FMT),
                "systemType": SYSTEM_TYPE,
            },
        )
        or [],
    )


def _fetch_robots() -> dict:
    return _load(
        "aicc:dash:robots",
        lambda: tanyibot.get("/apiEngine/robotCount/robotInfo", params={"systemType": SYSTEM_TYPE}) or {},
    )


def _fetch_running_jobs() -> list[dict]:
    page = _load(
        "aicc:dash:jobs",
        lambda: tanyibot.get(
            "/apiEngine/callStatsIndex/indexJobInfo",
            params={"pageNum": 1, "pageSize": 20, "jobStatus": "", "systemType": SYSTEM_TYPE},
        )
        or {},
    )
    return page.get("content") or []


def _render_stats(stats: dict, start: date, end: date) -> list[str]:
    call_total = _to_int(stats.get("taskCallTotal"))
    answered = _to_int(stats.get("answeredTaskTotal"))
    rate = stats.get("answeredTaskRate")
    rate_text = f"{_to_float(rate):.2f}%" if rate is not None else "暂无"
    avg_duration = _to_float(stats.get("averageDuration"))

    ab_total = _to_int(stats.get("customerABTotal"))
    ab_percent = stats.get("customerABPercent")
    ab_text = f"{ab_total} 个" + (f"（占比 {_to_float(ab_percent):.2f}%）" if ab_percent is not None else "")

    period = "今天" if start == end else f"{start} ~ {end}"
    lines = [
        f"【核心指标】{period}",
        f"- 呼叫量：{call_total} 通",
        f"- 接通量：{answered} 通，接通率 {rate_text}",
        f"- 平均通话时长：{avg_duration:.0f} 秒",
        f"- 客户 A/B 类：{ab_text}",
        f"- 短信：发送 {_to_int(stats.get('sendSmsTotal'))} 条，成功 {_to_int(stats.get('sendSmsSuccess'))} 条",
        f"- 加微：发起 {_to_int(stats.get('sendAddFriendCount'))}，通过 {_to_int(stats.get('sendAddFriendAdoptCount'))}",
    ]

    during = stats.get("highestCallTimeBO") or {}
    if during.get("hour") is not None:
        lines.append(f"- 接通率最高时段：{during['hour']} 点")

    if call_total == 0 and answered == 0:
        lines.append("- 注：该时间段没有外呼记录，指标为 0 是真实值。")

    return lines


def _render_intent(intent: dict) -> list[str]:
    if not intent:
        return ["【意向分布】暂无数据"]
    total = sum(_to_int(v) for v in intent.values())
    lines = ["【意向分布】"]
    for name, count in intent.items():
        value = _to_int(count)
        share = f"（{value / total * 100:.1f}%）" if total else ""
        lines.append(f"- {name}：{value} 个{share}")
    return lines


def _render_trend(trend: list[dict]) -> list[str]:
    if not trend:
        return ["【每日趋势】暂无数据"]
    lines = ["【每日趋势】", "日期        完成  接通  接通率"]
    for row in trend:
        lines.append(
            f"{row.get('statsDate') or '-'}  "
            f"{_to_int(row.get('taskTotalCompleted')):>4}  "
            f"{_to_int(row.get('answeredCall')):>4}  "
            f"{_to_float(row.get('answeredRate')):>6.2f}%"
        )
    return lines


def _render_jobs(robots: dict, jobs: list[dict]) -> list[str]:
    lines = [
        "【机器人与运行任务】",
        f"- AI 机器人：空闲 {_to_int(robots.get('freeRobotCount'))}，占用 {_to_int(robots.get('usedRobotCount'))}",
    ]
    if not jobs:
        lines.append("- 运行中任务：暂无")
        return lines

    lines.append(f"- 运行中任务（{len(jobs)} 个）：")
    for job in jobs[:10]:
        lines.append(
            f"  · {job.get('name') or '-'}"
            f"（状态 {job.get('status') or '-'}，机器人 {_to_int(job.get('robotCount'))} 个）"
        )
    if len(jobs) > 10:
        lines.append(f"  …… 另有 {len(jobs) - 10} 个未展示")
    return lines


@tool
def query_aicc_dashboard(start_date: str = "", end_date: str = "") -> str:
    """查询一知 AI 外呼首页看板：核心 KPI、意向分布、每日趋势、AI 机器人与运行中任务。

    Args:
        start_date: 开始日期，格式 YYYY-MM-DD；留空表示今天。
        end_date: 结束日期，格式 YYYY-MM-DD；留空表示今天。

    用于回答「今天外呼情况怎么样」「近 7 天接通率如何」「现在有几个任务在跑」这类问题。
    数据来自平台实时接口，结果会缓存 10 分钟，因此同一时间段内重复提问不会重复请求。
    """
    today = date.today()
    try:
        start = _parse_date(start_date, today)
        end = _parse_date(end_date, today)
    except ValueError:
        return "日期格式不正确，请使用 YYYY-MM-DD，例如 2026-11-01。"

    if start > end:
        start, end = end, start

    # 趋势固定看最近 8 天，避免单日口径下趋势图只有一个点
    trend_start = min(start, end - timedelta(days=7))

    try:
        stats = _fetch_stats(start, end)
        intent = _fetch_intent(start, end)
        trend = _fetch_trend(trend_start, end)
        robots = _fetch_robots()
        jobs = _fetch_running_jobs()
    except tanyibot.AiccError as exc:
        if exc.code == 400:
            return f"看板查询失败：{exc.message}（如持续出现，请检查 .env 中的账号密码或 Cookie）"
        return f"看板查询失败：{exc.message}（错误码 {exc.code}）"
    except Exception as exc:  # 网络抖动等非业务异常，如实返回，便于 agent 转述
        return f"看板查询失败：{exc}"

    parts: list[str] = []
    parts.extend(_render_stats(stats, start, end))
    parts.append("")
    parts.extend(_render_intent(intent))
    parts.append("")
    parts.extend(_render_trend(trend))
    parts.append("")
    parts.extend(_render_jobs(robots, jobs))
    parts.append("")
    parts.append(f"（数据缓存 {cache.DASHBOARD_TTL // 60} 分钟）")
    return "\n".join(parts)
