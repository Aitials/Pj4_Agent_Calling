"""客户档案查询工具：按企业名称 / 法人姓名 / 电话号码查本地 MySQL。

查询顺序（由数据层保证）：企业名称模糊 → 法人姓名模糊 → 电话号码精确。
命中后返回企业档案，并附带该企业名下全部关联号码。
"""

from __future__ import annotations

from langchain_core.tools import tool

from app.core.textfmt import display_width, pad
from app.repositories.customer_repo import DatabaseError, search_customers

PHONE_TYPE = {"MOBILE": "手机", "LANDLINE": "固话"}
INTENT = {"HIGH": "高", "MEDIUM": "中", "LOW": "低", "INVALID": "无效"}
BLOCK_REASON = {
    "EMPTY_NUMBER": "空号",
    "REJECTED": "已拒接",
    "NOT_OWNER": "非本人",
    "DO_NOT_DISTURB": "勿扰",
}
MATCH_LABEL = {
    "company_name": "企业名称",
    "legal_person": "法人姓名",
    "phone": "电话号码",
}

SUMMARY_COLUMNS = [
    ("序号", 4),
    ("企业名称", 28),
    ("法人", 10),
    ("地区", 14),
    ("最高意向", 8),
    ("号码数", 6),
]

DETAIL_LIMIT = 3
CONTACT_LIMIT = 10


def _fmt_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else "-"


def _fmt_date(value) -> str:
    return value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else ""


def _text(value, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _region(row: dict) -> str:
    region = (row.get("region") or "").strip()
    if region:
        return region
    parts = [str(row.get(key) or "").strip() for key in ("province", "city")]
    return "".join(part for part in parts if part)


def _contact_line(index: int, contact: dict) -> str:
    parts = [f"  {index}. {_text(contact.get('phone'))}"]

    phone_type = PHONE_TYPE.get(contact.get("phone_type"), contact.get("phone_type") or "")
    if phone_type:
        parts.append(phone_type)
    if contact.get("is_primary"):
        parts.append("主号码")
    if contact.get("contact_name"):
        parts.append(f"联系人：{contact['contact_name']}")
    if contact.get("call_times"):
        parts.append(f"已拨 {contact['call_times']} 次")

    intent = INTENT.get(contact.get("ai_intent_level"), contact.get("ai_intent_level") or "")
    if intent:
        parts.append(f"最近意向：{intent}")

    line = "  ".join(parts)

    flags = []
    if contact.get("is_blacklist"):
        flags.append("黑名单")
    block = BLOCK_REASON.get(contact.get("block_reason"), contact.get("block_reason") or "")
    if block:
        flags.append(block)
    if flags:
        line += "  [禁止拨打：" + "／".join(flags) + "]"
    return line


def _render_detail(row: dict, heading: str = "") -> str:
    lines: list[str] = []
    if heading:
        lines.append(heading)

    lines.append(f"企业名称：{_text(row.get('company_name'))}")
    lines.append(f"统一社会信用代码：{_text(row.get('credit_code'))}")
    lines.append(f"法定代表人：{_text(row.get('legal_person'), '未公示')}")
    lines.append(f"登记状态：{_text(row.get('registration_status'), '未公示')}")

    optional = [
        ("行业", row.get("industry")),
        ("企业类型", row.get("econ_kind")),
        ("所在地区", _region(row)),
        ("注册地址", row.get("address")),
        ("注册资本", row.get("registered_capital")),
        ("成立日期", _fmt_date(row.get("established_at"))),
        ("参保人数", row.get("insured_count")),
        ("官网", row.get("website")),
    ]
    for label, value in optional:
        if value is None or value == "":
            continue
        lines.append(f"{label}：{value}")

    call_times = int(row.get("call_times") or 0)
    last_call = _fmt_datetime(row.get("last_call_at"))
    if last_call != "-":
        lines.append(f"累计外呼：{call_times} 次（最近 {last_call}）")
    else:
        lines.append(f"累计外呼：{call_times} 次")

    best = INTENT.get(row.get("best_intent_level"), row.get("best_intent_level") or "")
    if best:
        lines.append(f"历史最高意向：{best}")
    last_intent = INTENT.get(row.get("last_intent_level"), row.get("last_intent_level") or "")
    if last_intent:
        lines.append(f"最近一次意向：{last_intent}")

    if row.get("is_blacklist"):
        lines.append("企业黑名单：是（建议不再外呼）")
    if row.get("note"):
        lines.append(f"备注：{row['note']}")

    contacts = row.get("contacts") or []
    if contacts:
        lines.append(f"关联号码（{len(contacts)} 个）：")
        for index, contact in enumerate(contacts[:CONTACT_LIMIT], start=1):
            lines.append(_contact_line(index, contact))
        if len(contacts) > CONTACT_LIMIT:
            lines.append(f"  …… 另有 {len(contacts) - CONTACT_LIMIT} 个号码未展示")
    else:
        lines.append("关联号码：暂无")

    return "\n".join(lines)


def _render_summary(rows: list[dict]) -> str:
    header = " | ".join(pad(name, width) for name, width in SUMMARY_COLUMNS)
    lines = [header, "-" * display_width(header)]

    for index, row in enumerate(rows, start=1):
        values = [
            str(index),
            _text(row.get("company_name")),
            _text(row.get("legal_person"), "-"),
            _text(_region(row), "-"),
            INTENT.get(row.get("best_intent_level"), "-"),
            str(len(row.get("contacts") or [])),
        ]
        lines.append(
            " | ".join(pad(value, width) for value, (_, width) in zip(values, SUMMARY_COLUMNS))
        )
    return "\n".join(lines)


@tool
def query_customer_profile(keyword: str, limit: int = 10) -> str:
    """查询客户（企业）档案，返回企业工商信息与该企业名下的联系电话。

    查询顺序依次兜底：① 企业名称模糊匹配 ② 法人姓名模糊匹配 ③ 电话号码精确匹配。
    因此企业名称、企业简称、法人名字、电话号码都可以直接作为关键字。

    Args:
        keyword: 查询关键字，如「云启」「杭州云启」「张明」「13800000001」。
        limit: 模糊匹配时最多返回的企业条数，默认 10，上限 50。

    命中一家企业返回完整档案（含全部关联号码）；命中多家返回摘要列表，
    并附上前 3 家企业的详细档案；未命中给出明确提示。
    """
    try:
        rows = search_customers(keyword, limit)
    except ValueError as exc:
        return str(exc)
    except DatabaseError as exc:
        return f"查询失败：{exc}"

    if not rows:
        return (
            f"未找到匹配「{keyword}」的客户。\n"
            "可尝试：企业名称或简称（如「云启」）、法人姓名（如「张明」）、完整电话号码。"
        )

    match_label = MATCH_LABEL.get(rows[0].get("match_type", ""), "")

    if len(rows) == 1:
        return _render_detail(rows[0])

    parts = [
        f"共命中 {len(rows)} 家企业（按{match_label}匹配），"
        f"下方展示摘要与前 {min(len(rows), DETAIL_LIMIT)} 家的详细档案。",
        "",
        _render_summary(rows),
    ]

    for index, row in enumerate(rows[:DETAIL_LIMIT], start=1):
        parts.append("")
        parts.append("-" * 24)
        parts.append(f"【{index}】详细档案")
        parts.append(_render_detail(row))

    if len(rows) > DETAIL_LIMIT:
        parts.append("")
        parts.append(f"其余 {len(rows) - DETAIL_LIMIT} 家仅展示摘要，可补充更精确的名称进一步查询。")

    return "\n".join(parts)
