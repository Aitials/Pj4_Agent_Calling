"""客户档案查询工具：按手机号或姓名查本地 MySQL，返回详情。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.clients.haokesou import CRM_CATE, LEVEL
from app.core.textfmt import display_width, pad
from app.repositories.customer_repo import DatabaseError, search_customers

SUMMARY_COLUMNS = [
    ("序号", 4),
    ("手机号", 15),
    ("姓名", 10),
    ("公司", 16),
    ("客户意愿", 10),
    ("等级", 4),
]


def _fmt_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else "-"


def _text(value, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _render_detail(row: dict) -> str:
    cate = CRM_CATE.get(row.get("crm_cate"), _text(row.get("crm_cate")))
    level = LEVEL.get(row.get("phone_level"), "未评级")
    lines = [
        f"手机号：{_text(row.get('phone'))}",
        f"姓名：{_text(row.get('name'))}",
        f"公司：{_text(row.get('company_name'))}",
        f"行业：{_text(row.get('industry'), '未填写')}",
        f"客户意愿：{cate}",
        f"意向等级：{level}",
        f"拨打次数：{_text(row.get('call_times'), '0')}",
        f"所属任务：{_text(row.get('task_name'))}",
        f"备注：{_text(row.get('note'))}",
        f"创建时间：{_fmt_datetime(row.get('source_created_at'))}",
    ]
    return "\n".join(lines)


def _render_summary(rows: list[dict]) -> str:
    header = " | ".join(pad(name, width) for name, width in SUMMARY_COLUMNS)
    lines = [f"共命中 {len(rows)} 条，按创建时间倒序：", "", header, "-" * display_width(header)]

    for index, row in enumerate(rows, start=1):
        values = [
            str(index),
            _text(row.get("phone")),
            _text(row.get("name")),
            _text(row.get("company_name")),
            CRM_CATE.get(row.get("crm_cate"), "-"),
            LEVEL.get(row.get("phone_level"), "-"),
        ]
        lines.append(
            " | ".join(pad(value, width) for value, (_, width) in zip(values, SUMMARY_COLUMNS))
        )

    lines.append("")
    lines.append("提示：可提供更完整的姓名或手机号进一步缩小范围。")
    return "\n".join(lines)


@tool
def query_customer_profile(keyword: str, limit: int = 10) -> str:
    """按手机号或姓名查询客户档案详情。

    Args:
        keyword: 查询关键字。纯数字且长度不小于 6 位时按手机号精确匹配，否则按姓名模糊匹配。
        limit: 模糊匹配时最多返回的条数，默认 10，上限 50。

    命中一条返回客户详情；命中多条返回摘要列表；未命中给出明确提示。
    """
    try:
        rows = search_customers(keyword, limit)
    except ValueError as exc:
        return str(exc)
    except DatabaseError as exc:
        return f"查询失败：{exc}"

    if not rows:
        return f"未找到匹配的客户：{keyword}"

    if len(rows) == 1:
        return _render_detail(rows[0])

    return _render_summary(rows)
