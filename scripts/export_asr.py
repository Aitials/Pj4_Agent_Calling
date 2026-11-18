"""按任务导出通话 ASR 转写文本（只读接口，不写一知侧数据）。

用法:
    python scripts/export_asr.py --job 1704345203463
    python scripts/export_asr.py --job 1704345203463 --start "2026-07-01 00:00:00" --end "2026-11-18 23:59:59"

输出:
    state/asr_out/<callRecordId>_<客户>.txt   逐轮对话
    state/asr_out/汇总.csv                    记录级字段汇总
    state/probe/asr/<callRecordId>.json       原始响应
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import time

import httpx

BASE = "https://aicc.tanyibot.com"
OUT_DIR = pathlib.Path("state/asr_out")
RAW_DIR = pathlib.Path("state/probe/asr")


def load_cookie() -> str:
    env = pathlib.Path(".env")
    m = re.search(r"^AICC_COOKIE=(.*)$", env.read_text(encoding="utf-8"), re.M)
    if not m or not m.group(1).strip():
        raise SystemExit("缺少 AICC_COOKIE")
    return m.group(1).strip()


def headers(cookie: str, form: bool = False) -> dict:
    # 注意 1：Referer 必须是外呼页面，指向会员中心会被 WAF 挡成 405
    # 注意 2：表单接口不能带 application/json，否则服务端按 JSON 解析报 412
    h = {
        "Cookie": cookie,
        "Origin": BASE,
        "Referer": BASE + "/active-callout-center/phoneTaskV2",
        "User-Agent": "Mozilla/5.0 Chrome/142",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    if form:
        # 必须显式覆盖 client 级默认的 application/json
        h["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        h["Content-Type"] = "application/json;charset=UTF-8"
    return h


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", text)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def list_records(client: httpx.Client, job: int, start: str, end: str, page_size: int = 50) -> list[dict]:
    body = {
        "robotCallJobIdSet": [job],
        "earliestCreationTime": start,
        "latestCreationTime": end,
    }
    out: list[dict] = []
    page = 1
    while True:
        r = client.post(f"/apiEngine/callRecord/getInfoListFromJob?pageNum={page}&pageSize={page_size}", content=json.dumps(body).encode())
        d = r.json()
        data = d.get("data") or {}
        content = data.get("content") or []
        out.extend(content)
        pages = data.get("pages") or 1
        if page >= pages or not content:
            break
        page += 1
        time.sleep(2.5)
    return out


def fetch_detail(client: httpx.Client, record_id: str) -> dict:
    r = client.post(
        "/apiEngine/callRecord/callDetailList",
        headers=headers(load_cookie(), form=True),
        data={"callRecordId": record_id, "type": "0", "simpleDebug": "true", "systemType": "CUSTOMER_CENTER"},
    )
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, required=True)
    ap.add_argument("--start", default="2020-01-01 00:00:00")
    ap.add_argument("--end", default="2030-12-31 23:59:59")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    h = headers(load_cookie())

    with httpx.Client(base_url=BASE, headers=h, timeout=45) as client:
        records = list_records(client, args.job, args.start, args.end)
        print(f"[列表] 任务 {args.job} 共 {len(records)} 条记录")
        rows = []
        for rec in records:
            rid = str(rec.get("callRecordId"))
            name = rec.get("customerPersonName") or "未知"
            rounds = rec.get("chatRound") or 0
            row = {
                "callRecordId": rid,
                "客户名": name,
                "电话": rec.get("calledPhoneNumber"),
                "通话状态": rec.get("resultStatus"),
                "意向": rec.get("aiIntentLevelName"),
                "意向code": rec.get("intentLevel"),
                "标签组": rec.get("intentLevelTagId"),
                "判定理由": rec.get("analysisBasis"),
                "轮次": rounds,
                "时长秒": rec.get("chatDuration"),
                "开始时间": rec.get("startTime"),
                "录音": rec.get("fullAudioUrl"),
                "导出文件": "",
            }
            if rounds > 0:
                time.sleep(2.5)
                d = fetch_detail(client, rid)
                (RAW_DIR / f"{rid}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                data = d.get("data") or {}
                lines = [
                    f"通话ID: {rid}",
                    f"客户: {name}  电话: {rec.get('calledPhoneNumber')}",
                    f"时间: {data.get('startTime')} ~ {data.get('endTime')}  时长: {data.get('chatDuration')}s  轮次: {data.get('chatRound')}",
                    f"状态: {data.get('resultStatus')}  意向: {data.get('intentLevelName')}（{data.get('intentLevelCode')}）",
                    f"理由: {data.get('analysisBasis')}",
                    f"录音: {data.get('fullAudioUrl')}",
                    "-" * 60,
                ]
                for item in data.get("callDetailList") or []:
                    who = "机器人" if item.get("type") == "ROBOT" else "客户"
                    off = ""
                    if item.get("startOffset") is not None:
                        off = f" [{item.get('startOffset')}~{item.get('endOffset')}ms]"
                    lines.append(f"{who}{off}: {strip_html(item.get('text'))}")
                fp = OUT_DIR / f"{rid}_{name}.txt"
                fp.write_text("\n".join(lines), encoding="utf-8")
                row["导出文件"] = str(fp)
                print(f"  ✅ {rid} 轮次 {rounds} → {fp}")
            else:
                print(f"  ·  {rid} 轮次 0（未接通/无对话），跳过文本导出")
            rows.append(row)

        if rows:
            csv_path = OUT_DIR / "汇总.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            print(f"[汇总] {csv_path}")


if __name__ == "__main__":
    main()
