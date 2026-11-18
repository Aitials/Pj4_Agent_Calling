"""一知内部通话记录接口：列表、详情、意向标签组。

与 `tanyibot.request` 的差异：
- 需要 Referer / Origin，否则 WAF 可能返回 405
- 详情接口是表单提交，不是 JSON
- 未登录时复用 tanyibot.login() 自动续期

字段与 state/probe/asr 下的样本一致。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.clients import tanyibot

log = logging.getLogger("aicc_call_record")

REFERER = tanyibot.BASE_URL + "/active-callout-center/phoneTaskV2"
LIST_PATH = "/apiEngine/callRecord/getInfoListFromJob"
DETAIL_PATH = "/apiEngine/callRecord/callDetailList"
INTENT_TAG_PATH = "/apiEngine/intentLevelTag/list"


def _headers(json_body: bool) -> dict[str, str]:
    return {
        "Cookie": tanyibot.current_cookie(),
        "Origin": tanyibot.BASE_URL,
        "Referer": REFERER,
        "User-Agent": tanyibot.UA,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": (
            "application/json;charset=UTF-8"
            if json_body
            else "application/x-www-form-urlencoded"
        ),
    }


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    form_data: dict[str, Any] | None = None,
    retry_login: bool = True,
) -> Any:
    headers = _headers(json_body is not None)
    with httpx.Client(
        base_url=tanyibot.BASE_URL,
        timeout=45,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        response = client.request(
            method.upper(),
            path,
            params=params,
            json=json_body,
            data=form_data,
            headers=headers,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise tanyibot.AiccError(
            None, f"响应不是 JSON（HTTP {response.status_code}）：{response.text[:200]}"
        ) from exc

    code = payload.get("code")
    message = payload.get("resultMsg") or payload.get("message") or ""
    if code == 200:
        return payload.get("data")

    if retry_login and code == 400 and "未登录" in message:
        log.info("通话记录接口 Cookie 已失效，重新登录")
        tanyibot.login()
        return _request(
            method,
            path,
            params=params,
            json_body=json_body,
            form_data=form_data,
            retry_login=False,
        )

    raise tanyibot.AiccError(code, message or f"接口返回异常（HTTP {response.status_code}）")


def list_records(
    job_id: int,
    start: str,
    end: str,
    *,
    page_size: int = 50,
    max_pages: int = 10,
) -> list[dict]:
    """按任务 + 时间窗拉通话列表，自动翻页。"""
    body = {
        "robotCallJobIdSet": [int(job_id)],
        "earliestCreationTime": start,
        "latestCreationTime": end,
    }
    records: list[dict] = []
    for page in range(1, max(1, max_pages) + 1):
        data = _request(
            "POST",
            LIST_PATH,
            params={"pageNum": page, "pageSize": page_size},
            json_body=body,
        ) or {}
        rows = data.get("content") or []
        records.extend(row for row in rows if isinstance(row, dict))
        pages = int(data.get("pages") or 1)
        if page >= pages or not rows:
            break
    return records


def fetch_detail(record_id: str | int) -> dict:
    """拉单通详情，包含 callDetailList、意向、录音、动态变量。"""
    data = _request(
        "POST",
        DETAIL_PATH,
        form_data={
            "callRecordId": str(record_id),
            "type": "0",
            "simpleDebug": "true",
            "systemType": "CUSTOMER_CENTER",
        },
    )
    return data if isinstance(data, dict) else {}


def list_intent_groups(page_size: int = 100) -> list[dict]:
    """拉意向标签组列表，结构为 {content: [{intentLevelTagId, name, details: [...]}]}。"""
    data = _request(
        "GET",
        INTENT_TAG_PATH,
        params={"pageSize": page_size, "systemType": "CALL_OUT"},
    )
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("content") or data.get("data") or []
        return [row for row in rows if isinstance(row, dict)]
    return []
