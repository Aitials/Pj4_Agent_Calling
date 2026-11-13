"""好客搜接口探测 v3 - 慢速逐个测（接口说明.md 第四节那 22 个）

每请求间隔 2.5 秒，避免触发频率限制。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.clients.haokesou_auth import ensure_cookie  # noqa: E402

COOKIE = ensure_cookie()
assert COOKIE, "Cookie 不可用"

COOKIES: dict[str, str] = {}
for part in COOKIE.split(";"):
    if "=" in part:
        k, v = part.strip().split("=", 1)
        COOKIES[k] = v

BASE = "http://whxt.jshks.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def build_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": UA,
        "Referer": referer,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }


# 按接口说明.md 第四节登记的 22 个待测接口
# 每项: (模块, 名称, 页面 Referer, 推测 AJAX 路径, POST 表单)
ENDPOINTS: list[tuple[str, str, str, str, dict]] = [
    # A. 销售管理 9 个
    ("销售管理", "销售账号管理",
     "/user/manager/account_management", "/user/manager/ajax_account",
     {"page": "1", "limit": "10"}),
    ("销售管理", "充值管理",
     "/user/manager/recharge_management", "/user/manager/ajax_recharge",
     {"page": "1", "limit": "10"}),
    ("销售管理", "机器人管理",
     "/user/manager/robot_management", "/user/manager/ajax_robot",
     {"page": "1", "limit": "10"}),
    ("销售管理", "线路管理",
     "/user/manager/line_management", "/user/manager/ajax_route",
     {"page": "1", "limit": "10"}),
    ("销售管理", "ASR 管理",
     "/user/asr/list", "/user/asr/ajax_list",
     {"page": "1", "limit": "10"}),
    ("销售管理", "短信通道",
     "/user/sms/channel", "/user/sms/channel_ajax",
     {"page": "1", "limit": "10"}),
    ("销售管理", "资费管理",
     "/user/manager/tariff_management", "/user/manager/ajax_tariff",
     {"page": "1", "limit": "10"}),
    ("销售管理", "操作记录",
     "/user/manager/operation_record", "/user/manager/ajax_log",
     {"page": "1", "limit": "10"}),
    ("销售管理", "服务费管理",
     "/user/manager/service_cost", "/user/manager/ajax_service",
     {"page": "1", "limit": "10"}),

    # B. 话术管理 2 个
    ("话术管理", "话术配置",
     "/user/scenarios/scene", "/user/scenarios/ajax_scene",
     {"page": "1", "limit": "10"}),
    ("话术管理", "下发记录",
     "/user/scenarios/sendout_scene", "/user/scenarios/ajax_sendout_scene",
     {"page": "1", "limit": "10"}),

    # C. CRM 2 个
    ("CRM", "坐席管理",
     "/user/member/seat_management", "/user/member/ajax_seat_management",
     {"page": "1", "limit": "10"}),
    ("CRM", "客户详情弹窗",
     "/user/member/intentional_member", "/user/Member/get_constomerDetail",
     {"mobile": "13800001111"}),  # 需要真实客户 ID，这里只测路径是否 200+JSON

    # D. 任务管理 3 个
    ("任务管理", "任务模板",
     "/user/plan/task_template", "/user/plan/task_template_ajax",
     {"page": "1", "limit": "10"}),
    ("任务管理", "号码管理",
     "/user/plan/phone_manage", "/user/plan/phone_manage_ajax",
     {"page": "1", "limit": "10"}),
    ("任务管理", "下发记录",
     "/user/plan/sendout_phone", "/user/plan/ajax_sendout",
     {"page": "1", "limit": "10"}),

    # E. 短信管理 3 个
    ("短信管理", "短信签名",
     "/user/sms/signature", "/user/sms/ajax_signature",
     {"page": "1", "limit": "10"}),
    ("短信管理", "短信模板",
     "/user/sms/template", "/user/sms/ajax_template",
     {"page": "1", "limit": "10"}),
    ("短信管理", "消费记录",
     "/user/sms/statistics", "/user/sms/ajax_consume",
     {"page": "1", "limit": "10"}),

    # F. 财务管理 3 个
    ("财务管理", "概况",
     "/user/enterprise/admin_survey", "/user/enterprise/ajax_survey",
     {"page": "1", "limit": "10"}),
    ("财务管理", "消费统计",
     "/user/enterprise/admin_consumption_statistics", "/user/enterprise/ajax_consume_statistics",
     {"page": "1", "limit": "10"}),
    ("财务管理", "消费明细",
     "/user/enterprise/consumption_detail", "/user/enterprise/ajax_consume_detail",
     {"page": "1", "limit": "10"}),
]


def main() -> None:
    results: list[dict] = []
    print(f"共 {len(ENDPOINTS)} 个待测，每请求间隔 2.5s，预计耗时 ~{len(ENDPOINTS) * 3}s\n")

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for idx, (mod, name, referer, path, params) in enumerate(ENDPOINTS, 1):
            time.sleep(2.5)
            headers = build_headers(referer)
            url = BASE + path
            try:
                r = client.post(url, headers=headers, cookies=COOKIES, data=params)
                body = r.text.strip()

                code = msg = total = None
                is_json = False
                try:
                    j = r.json()
                    is_json = True
                    if isinstance(j, dict):
                        code = j.get("code")
                        msg = j.get("msg")
                        data = j.get("data")
                        if isinstance(data, dict):
                            total = data.get("total") or data.get("count")
                except Exception:
                    pass

                has_error_page = ("ErrorException" in body
                                  or "<!DOCTYPE html>" in body[:80]
                                  or "System Error" in body)
                ok = (r.status_code == 200) and is_json and not has_error_page
                mark = "✅" if ok else ("⚠️" if r.status_code == 200 else "❌")

                results.append({
                    "module": mod, "name": name, "path": path, "referer": referer,
                    "status": r.status_code, "code": code, "msg": msg,
                    "total": total, "is_json": is_json, "ok": ok,
                    "body_preview": body[:200],
                })
                print(f"[{idx:02d}/{len(ENDPOINTS)}] {mark} {mod}/{name:<14} {path:<45} → {r.status_code} code={code} total={total}")
                if not ok and r.status_code != 200:
                    print(f"     preview: {body[:120]}")
            except httpx.TimeoutException:
                results.append({"module": mod, "name": name, "path": path,
                                "ok": False, "error": "timeout"})
                print(f"[{idx:02d}] ❌ {mod}/{name} TIMEOUT")
            except Exception as exc:
                results.append({"module": mod, "name": name, "path": path,
                                "ok": False, "error": str(exc)[:80]})
                print(f"[{idx:02d}] ❌ {mod}/{name} ERR: {str(exc)[:60]}")

    # 汇总
    print(f"\n{'=' * 80}")
    good = [r for r in results if r.get("ok")]
    print(f"✅ 可用: {len(good)}/{len(results)}")
    for r in good:
        print(f"   {r['module']:<6} {r['name']:<12} {r['path']:<45} code={r['code']} total={r['total']}")

    bad = [r for r in results if not r.get("ok")]
    if bad:
        print(f"\n❌ 不可用/待重试: {len(bad)}")
        for r in bad:
            reason = r.get("error") or f"status={r.get('status')} code={r.get('code')} json={r.get('is_json')}"
            print(f"   {r['module']:<6} {r['name']:<12} {r['path']:<45} → {reason}")

    out = Path("state/_probe_v3_tomorrow.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写 {out}")


if __name__ == "__main__":
    main()
