"""抓取一知智能 AICC 开放平台官方接口文档（176 篇 Markdown），合并为单个 md。

接口:
    GET /apiEngine/apiDocument/selectAllLevelApiDocumentMenuList
    GET /apiEngine/apiDocument/getArticleById?id=<apiDocumentMenuId>

产物:
    state/probe/openapi_docs/<id>.md      单篇原文
    state/probe/openapi_all.json          含目录路径的完整数据
    一知智能AICC开放平台接口文档（官方）.md   合并版（带目录）
"""

from __future__ import annotations

import json
import pathlib
import re
import time

import httpx

BASE = "https://aicc.tanyibot.com"
RAW_DIR = pathlib.Path("state/probe/openapi_docs")
MERGED = pathlib.Path("AI messages/一知智能AICC开放平台接口文档（官方）.md")
SLEEP = 0.35


def load_cookie() -> str:
    m = re.search(r"^AICC_COOKIE=(.*)$", pathlib.Path(".env").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("缺少 AICC_COOKIE")
    return m.group(1).strip()


def headers(cookie: str) -> dict:
    return {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 Chrome/142",
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE + "/active-main/open_api?docId=46",
        "X-Requested-With": "XMLHttpRequest",
    }


def walk(nodes, trail, out):
    for n in nodes:
        name = n.get("name") or ""
        path = trail + [name]
        if n.get("type") == "article":
            out.append({"id": n.get("apiDocumentMenuId"), "name": name, "path": path})
        for c in n.get("next") or []:
            walk([c], path, out)


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    h = headers(load_cookie())

    with httpx.Client(base_url=BASE, headers=h, timeout=45) as client:
        menu = client.get("/apiEngine/apiDocument/selectAllLevelApiDocumentMenuList").json()
        articles: list[dict] = []
        walk(menu.get("data") or [], [], articles)
        print(f"[目录] {len(articles)} 篇")

        for i, a in enumerate(articles, 1):
            fp = RAW_DIR / f"{a['id']}.md"
            if fp.exists() and fp.stat().st_size > 0:
                a["content"] = fp.read_text(encoding="utf-8")
                continue
            r = client.get("/apiEngine/apiDocument/getArticleById", params={"id": a["id"]})
            try:
                d = r.json()
            except Exception:
                print(f"  ! {a['id']} 解析失败 status={r.status_code}")
                a["content"] = ""
                continue
            data = d.get("data") or {}
            a["content"] = data.get("content") or ""
            a["updateTime"] = data.get("updateTime")
            a["createTime"] = data.get("createTime")
            fp.write_text(a["content"] or "", encoding="utf-8")
            if i % 20 == 0 or i == len(articles):
                print(f"  进度 {i}/{len(articles)}  ({a['path'][-1]})")
            time.sleep(SLEEP)

    pathlib.Path("state/probe/openapi_all.json").write_text(
        json.dumps(articles, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 合并成单个 md
    lines = [
        "# 一知智能 AICC 开放平台 官方接口文档",
        "",
        "> 来源：一知智能 AICC 开放平台（`https://aicc.tanyibot.com/active-main/open_api`）",
        f"> 抓取时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"> 章节数：{len(articles)} 篇",
        "> 说明：本文档为官方在线接口文档的完整转写，内容与线上一致，未做改写。",
        "",
        "## 目录",
        "",
    ]
    for idx, a in enumerate(articles, 1):
        path = " / ".join(a["path"][:-1])
        indent = "  " * max(0, len(a["path"]) - 2)
        lines.append(f"{indent}- {idx}. {a['name']}" + (f"（{path}）" if path else ""))
    lines.append("")

    for idx, a in enumerate(articles, 1):
        lines.append("")
        lines.append("---")
        lines.append("")
        depth = min(len(a["path"]) + 1, 6)
        lines.append(f"{'#' * depth} {idx}. {a['name']}")
        if len(a["path"]) > 1:
            lines.append("")
            lines.append(f"> 所属模块：{' / '.join(a['path'][:-1])}")
        lines.append("")
        lines.append((a.get("content") or "").strip())
        lines.append("")

    MERGED.write_text("\n".join(lines), encoding="utf-8")
    total_chars = sum(len(a.get("content") or "") for a in articles)
    print(f"[完成] 合并版 {MERGED} | 正文 {total_chars} 字符 | {len(articles)} 篇")


if __name__ == "__main__":
    main()
