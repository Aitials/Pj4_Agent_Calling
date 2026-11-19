"""客户分析简报：把分析结果、通话记录、企业档案拼成固定格式 Markdown。

纯字符串拼接，不调用模型；生成后本地留档，供上传与排查。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.cards.high_intent_card import build_task_description
from app.core.config import STATE_DIR

log = logging.getLogger("brief")

BRIEF_DIR = STATE_DIR / "briefs"
DESCRIPTION_LIMIT = 3000

INTENT_LABELS = {"HIGH": "高意向", "MEDIUM": "中意向", "LOW": "低意向", "INVALID": "无效"}
CALL_STATUS = {
    "ANSWERED": "已接通",
    "NO_ANSWER": "未接听",
    "INVALID": "空号/无效",
    "REJECTED": "拒接",
    "FAILED": "呼叫失败",
    "UNKNOWN": "未知",
}


def build_markdown(record: dict, analysis: dict, profile: dict | None) -> str:
    """生成简报正文。三份数据缺失时用占位符，保证结构稳定。"""
    company = _company(record, profile)
    version = analysis.get("version") or 1
    lines = [
        f"# 客户分析简报 · {company}",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 分析版本：v{version}",
        f"- 通话记录：{record.get('record_id') or '-'}",
        "",
        "## 一、客户与企业",
        f"- 企业名称：{company}",
        f"- 统一社会信用代码：{_text((profile or {}).get('credit_code') or record.get('credit_code'))}",
        f"- 法定代表人：{_text((profile or {}).get('legal_person'), '未公示')}",
        f"- 行业：{_text((profile or {}).get('industry'))}",
        f"- 所在地区：{_text((profile or {}).get('region'))}",
        f"- 联系电话：{_text(record.get('customer_phone'))}",
        f"- 历史外呼：{int((profile or {}).get('call_times') or 0)} 次",
        "",
        "## 二、通话概况",
        f"- 通话时间：{_dt(record.get('call_time'))}",
        f"- 通话时长：{record.get('duration') or 0} 秒（{record.get('chat_round') or 0} 轮）",
        f"- 通话状态：{CALL_STATUS.get(str(record.get('call_status') or ''), _text(record.get('call_status')))}",
        f"- 一知意向：{_text(record.get('supplier_level'))}"
        f"（tag {record.get('intent_level_tag_id')} / code {record.get('intent_level_code')}）",
        f"- 挂断方：{_text(record.get('hangup_by'))}",
        f"- 录音链接：{_text(record.get('record_url'))}",
        "",
        "## 三、AI 分析结论",
        f"- 意向等级：{INTENT_LABELS.get(str(analysis.get('intent_level') or '').upper(), _text(analysis.get('intent_level')))}",
        f"- 判定理由：{_text(analysis.get('reason'))}",
        f"- 客户原话：{_text(analysis.get('quote'))}",
    ]
    lines.extend(_bullets("客户需求", analysis.get("customer_needs")))
    lines.extend(_bullets("客户关注点", analysis.get("concerns")))
    lines.extend(_bullets("客户异议", analysis.get("objections")))
    lines.extend(_bullets("意向判断依据", analysis.get("intent_evidence")))

    lines.extend(["", "## 四、销售跟进话术"])
    opening = _text(analysis.get("opening"), "")
    if opening:
        lines.append(f"**开场话术**：{opening}")
    lines.extend(_bullets("产品价值切入点", analysis.get("value_points")))
    lines.extend(_bullets("异议处理", analysis.get("objection_handling")))
    next_action = _text(analysis.get("next_action"), "")
    if next_action:
        lines.append(f"**下一步行动**：{next_action}")
    best_at = _text(analysis.get("best_follow_at"), "")
    if best_at:
        lines.append(f"**建议联系时间**：{best_at}")

    lines.extend(["", "## 五、完整对话转写", ""])
    transcript = str(record.get("asr_transcript") or "").strip()
    lines.append(transcript or "（无转写）")
    lines.append("")
    return "\n".join(lines)


def save_local(record_id: str, version: int, content: str, company: str = "") -> dict:
    """写入版本文件与 latest 文件，返回路径信息。"""
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    safe_company = _sanitize(company) or "客户"
    safe_record = _sanitize(record_id) or "record"
    version_name = f"客户分析简报_{safe_company}_{safe_record}_v{version}.md"
    version_path = BRIEF_DIR / version_name
    latest_path = BRIEF_DIR / f"{safe_record}_latest.md"
    version_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
    return {
        "filename": version_name,
        "version_path": str(version_path),
        "latest_path": str(latest_path),
    }


def build_task_description_with_attachment(record: dict, filename: str) -> str:
    """重建原任务描述并追加附件提示；总长度控制在飞书限制内。"""
    hint = f"\n\n详见附件：{filename}"
    base = build_task_description(record)
    limit = max(0, DESCRIPTION_LIMIT - len(hint))
    return base[:limit] + hint


def company_of(record: dict, profile: dict | None) -> str:
    return _company(record, profile)


def _company(record: dict, profile: dict | None) -> str:
    return (
        _text((profile or {}).get("company_name"), "")
        or str(record.get("company_name") or "").strip()
        or str(record.get("customer_name") or "").strip()
        or str(record.get("customer_phone") or "").strip()
        or "未知客户"
    )


def _bullets(title: str, value: Any) -> list[str]:
    items = _as_list(value)
    if not items:
        return [f"- {title}：未提及"]
    lines = [f"- {title}："]
    lines.extend(f"  - {item}" for item in items)
    return lines


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except ValueError:
            return [text]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _text(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text or default


def _dt(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d %H:%M")
    return _text(value)


def _sanitize(value: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\s]+', "_", str(value or "").strip())
    return text[:60]
