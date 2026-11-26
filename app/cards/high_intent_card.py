"""高意向客户通知卡片与飞书任务文案。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

MAX_TRANSCRIPT_LINES = 6
MAX_LINE_CHARS = 120


def build_task_summary(record: dict) -> str:
    name = _company_or_phone(record)
    return f"高意向客户跟进｜{name}"[:3000]


def build_task_description(record: dict) -> str:
    lines = [
        f"企业：{_company_or_phone(record)}",
        f"电话：{record.get('customer_phone') or '-'}",
        f"一知意向：{record.get('supplier_level') or '-'}（code {record.get('intent_level_code')}）",
        f"判定理由：{record.get('analysis_basis') or '-'}",
        f"关注点：{record.get('customer_concern') or '-'}",
        f"通话：{_format_dt(record.get('call_time'))} · {record.get('duration') or 0}s",
        f"转写摘要：\n{_transcript_summary(record)}",
    ]
    url = record.get("record_url")
    if url:
        lines.append(f"录音：{url}")
    return "\n".join(lines)[:3000]


def build_high_intent_card(record: dict, notice: dict, task_guid: str | None = None) -> dict:
    """构造 Card JSON 2.0，展示企业、意向、理由、转写摘要与录音。"""
    title = f"高意向客户 · {_company_or_phone(record)}"
    lines = [
        f"**企业**：{_company_or_phone(record)}",
        f"**电话**：{record.get('customer_phone') or notice.get('trigger_phone') or '-'}",
        f"**一知意向**：{record.get('supplier_level') or '-'}"
        f"（code {record.get('intent_level_code')}）",
        f"**判定理由**：{record.get('analysis_basis') or '-'}",
        f"**客户关注点**：{record.get('customer_concern') or '-'}",
        f"**通话**：{_format_dt(record.get('call_time'))} · "
        f"{record.get('duration') or 0}s · {record.get('chat_round') or 0} 轮",
        "",
        "**转写摘要**",
        _transcript_summary(record),
        "",
        _recording_line(record),
    ]
    if task_guid:
        lines.append(
            f"[👉 查看飞书任务](https://applink.feishu.cn/client/todo/detail?guid={task_guid})"
        )

    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title[:100]},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
            ]
        },
    }


def _company_or_phone(record: dict) -> str:
    return (
        str(record.get("company_name") or "").strip()
        or str(record.get("customer_name") or "").strip()
        or str(record.get("customer_phone") or "").strip()
        or str(record.get("trigger_phone") or "").strip()
        or "未知客户"
    )


def _format_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value or "-")


def _transcript_summary(record: dict) -> str:
    transcript = str(record.get("asr_transcript") or "").strip()
    if not transcript:
        return "> 无转写（未接通或平台未返回）"

    lines = [line.strip() for line in transcript.splitlines() if line.strip()]
    if len(lines) > MAX_TRANSCRIPT_LINES:
        lines = lines[: MAX_TRANSCRIPT_LINES - 1] + ["……"] + lines[-1:]
    quoted = ["> " + line[:MAX_LINE_CHARS] for line in lines]
    return "\n".join(quoted)


def _recording_line(record: dict) -> str:
    url = str(record.get("record_url") or "").strip()
    if not url:
        return "**录音**：暂无"

    expire_at = record.get("record_expire_at")
    if isinstance(expire_at, datetime):
        expire_at = expire_at.date()
    if isinstance(expire_at, date):
        days = (expire_at - date.today()).days
        if days < 0:
            return f"**录音**：已过期（{expire_at}）"
        if days <= 3:
            return f"**录音**：[点击收听]({url})（{days} 天后过期）"
        return f"**录音**：[点击收听]({url})（有效期至 {expire_at}）"
    return f"**录音**：[点击收听]({url})"


# ---------------------------------------------------------------------------
# 钉钉派单文案：私聊 markdown + 待办描述（本期不带附件）
# ---------------------------------------------------------------------------

INTENT_LABELS = {"HIGH": "高意向", "MEDIUM": "中意向", "LOW": "低意向", "INVALID": "无效"}


def build_dingtalk_todo_subject(record: dict) -> str:
    """钉钉待办标题：与飞书任务标题保持一致。"""
    return f"高意向客户跟进｜{_company_or_phone(record)}"[:200]


def build_dingtalk_todo_description(record: dict, notice: dict) -> str:
    """待办描述带上通话记录 ID，保证不同通话内容不同、同一通知重试内容一致。"""
    lines = [
        f"企业：{_company_or_phone(record)}",
        f"电话：{record.get('customer_phone') or notice.get('trigger_phone') or '-'}",
        f"一知意向：{record.get('supplier_level') or '-'}"
        f"（code {record.get('intent_level_code')}）",
        f"判定理由：{record.get('analysis_basis') or '-'}",
        f"客户关注点：{record.get('customer_concern') or '-'}",
        f"通话：{_format_dt(record.get('call_time'))} · {record.get('duration') or 0}s",
        f"通话记录：{record.get('record_id') or notice.get('trigger_record_id') or '-'}",
    ]
    url = str(record.get("record_url") or "").strip()
    if url:
        lines.append(f"录音：{url}")
    return "\n".join(lines)[:4000]


def build_dingtalk_notice_text(record: dict, notice: dict) -> str:
    """钉钉单聊通知：字段做减法，只保留销售跟进必需的几项。"""
    lines = [
        f"**高意向客户**：{_company_or_phone(record)}",
        f"**电话**：{record.get('customer_phone') or notice.get('trigger_phone') or '-'}",
        f"**一知意向**：{record.get('supplier_level') or '-'}"
        f"（code {record.get('intent_level_code')}）",
        f"**判定理由**：{record.get('analysis_basis') or '-'}",
        f"**客户关注点**：{record.get('customer_concern') or '-'}",
        f"**通话**：{_format_dt(record.get('call_time'))} · {record.get('duration') or 0}s",
        "",
        "待办已创建，请及时跟进。",
    ]
    return "\n".join(lines)


def build_dingtalk_analysis_text(record: dict, analysis: dict, notice: dict) -> str:
    """AI 分析通知：钉钉不更新原卡，另发一条较短的 markdown。"""
    intent = INTENT_LABELS.get(
        str(analysis.get("intent_level") or "").upper(),
        str(analysis.get("intent_level") or "-"),
    )
    lines = [
        f"**AI 分析**：{_company_or_phone(record)}",
        f"**电话**：{record.get('customer_phone') or notice.get('trigger_phone') or '-'}",
        f"**意向**：{intent}（一知 {record.get('supplier_level') or '-'}）",
    ]
    reason = _clip(analysis.get("reason"), 120)
    if reason:
        lines.append(f"**判定理由**：{reason}")
    quote = _clip(analysis.get("quote"), 100)
    if quote:
        lines.append(f"**客户原话**：{quote}")
    needs = _first_item(analysis.get("customer_needs"))
    if needs:
        lines.append(f"**客户需求**：{needs}")
    objections = _first_item(analysis.get("objections"))
    if objections:
        lines.append(f"**客户异议**：{objections}")
    next_action = _clip(analysis.get("next_action"), 120)
    if next_action:
        lines.append(f"**下一步**：{next_action}")
    best_at = _clip(analysis.get("best_follow_at"), 60)
    if best_at:
        lines.append(f"**建议联系时间**：{best_at}")
    return "\n".join(lines)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit] if text else ""


def _first_item(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text[:100]
        return ""
    text = str(value or "").strip()
    if text.startswith("["):
        try:
            import json

            parsed = json.loads(text)
        except ValueError:
            return text[:100]
        if isinstance(parsed, list):
            for item in parsed:
                item_text = str(item or "").strip()
                if item_text:
                    return item_text[:100]
    return text[:100]
