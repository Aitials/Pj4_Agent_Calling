"""好客搜客户导出工具：供 Agent 调用，返回可直接展示的客户表格文本。"""

from __future__ import annotations

from langchain_core.tools import tool

from app.clients.haokesou import CookieExpiredError, collect_customers, format_customers


@tool
def export_haokesou_customers(limit: int = 50, max_pages: int = 200) -> str:
    """从好客搜后台导出客户资料（含客户意愿、意向等级、拨打次数等）。

    Args:
        limit: 每页条数，默认 50。
        max_pages: 最大翻页数，默认 200，用于防止异常时无限翻页。

    返回客户表格文本；Cookie 失效或接口异常时返回错误说明，由 Agent 转述给用户。
    """
    try:
        rows = collect_customers(limit=limit, max_pages=max_pages)
    except CookieExpiredError:
        return "好客搜 Cookie 已失效，请重新登录后台获取 PHPSESSID 后重试。"
    except RuntimeError as exc:
        return f"导出失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return f"导出失败：{type(exc).__name__}: {exc}"

    if not rows:
        return "好客搜当前没有客户数据。"

    return f"共导出 {len(rows)} 条客户记录：\n\n{format_customers(rows)}"
