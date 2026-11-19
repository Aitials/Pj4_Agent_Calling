"""分析结果通知卡片：复用基础通知卡片，补充 AI 分析与销售话术。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

MAX_ITEMS = 6

INTENT_LABELS = {"HIGH": "高意向", "MEDIUM": "中意向", "LOW": "低意向", "INVALID": "无效"}


def build_analysis_card(record: dict, analysis: dict, notice: dict) -> dict:
    """构造 Card JSON 2.0：基础信息 + 结构化分析 + 跟进话术。"""
    name = _company_or_phone(record)
    title = f"AI 分析 · {name}"
    lines = [
        f"**企业**：{name}",
        f"**电话**：{record.get('customer_phone') or notice.get('trigger_phone') or '-'}",
        f"**意向**：{_intent(analysis.get('intent_level'))}"
        f"（一知 {record.get('supplier_level') or '-'}）",
        f"**通话**：{_dt(record.get('call_time'))} · {record.get('duration') or 0}s"
        f" · {record.get('chat_round') or 0} 轮",
    ]
    reason = _text(analysis.get("reason"))
    if reason:
        lines.append(f"**判定理由**：{reason}")
    quote = _text(analysis.get("quote"))
    if quote:
        lines.append(f"**客户原话**：> {quote}")

    lines.extend(_section("客户需求", analysis.get("customer_needs")))
    lines.extend(_section("关注点", analysis.get("concerns")))
    lines.extend(_section("客户异议", analysis.get("objections")))
    lines.extend(_section("意向依据", analysis.get("intent_evidence")))
    lines.extend(_section("价值切入点", analysis.get("value_points")))
    lines.extend(_section("异议处理", analysis.get("objection_handling")))

    opening = _text(analysis.get("opening"))
    if opening:
        lines.extend(["", "**开场话术**", f"> {opening}"])
    next_action = _text(analysis.get("next_action"))
    if next_action:
        lines.extend(["", f"**下一步**：{next_action}"])
    best_at = _text(analysis.get("best_follow_at"))
    if best_at:
        lines.append(f"**建议联系时间**：{best_at}")

    guid = notice.get("feishu_task_guid")
    if guid:
        lines.append(f"[👉 查看飞书任务](https://applink.feishu.cn/client/todo/detail?guid={guid})")
    url = str(record.get("record_url") or "").strip()
    if url:
        lines.append(f"[🎧 收听录音]({url})")

    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title[:100]},
            "template": "green",
        },
        "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}]},
    }


def _section(title: str, value: Any) -> list[str]:
    items = _list(value)
    if not items:
        return []
    lines = ["", f"**{title}**"]
    for item in items[:MAX_ITEMS]:
        lines.append(f"- {item}")
    if len(items) > MAX_ITEMS:
        lines.append(f"- …… 另有 {len(items) - MAX_ITEMS} 条")
    return lines


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except ValueError:
            return [text]
        value = parsed
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _intent(value: Any) -> str:
    code = str(value or "").upper()
    return INTENT_LABELS.get(code, code or "-")


def _company_or_phone(record: dict) -> str:
    return (
        str(record.get("company_name") or "").strip()
        or str(record.get("customer_name") or "").strip()
        or str(record.get("customer_phone") or "").strip()
        or "未知客户"
    )


def _dt(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value or "-")
