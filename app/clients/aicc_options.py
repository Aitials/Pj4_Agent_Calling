"""一知 AICC 表单选项：发卡前实时拉取，短缓存。

飞书卡片的下拉选项是发送那一刻的快照，客户端不会在展开时再请求服务端，
因此所有选项都在「构建卡片之前」拉一次；缓存 5 分钟，避免用户连续刷新打爆接口。

接口实测（2026-11-17）：
- 线路       GET  /apiEngine/tenantPhoneNumber/getActive
- 话术 BOT   POST /apiDialogFlow/dialogFlow/getDialogFlowInfoList  {"pageNum":1,"pageSize":100}
- 短信模板   GET  /apiEngine/smsTemplate/list?pageNum=1&pageSize=100
- 坐席组     GET  /apiEngine/csRobot/staffGroupList?pageNum=1&pageSize=100
- 客户分组   GET  /aiccWeb/rcs/customerBlackGroup/selectCustomerBlackGroupPageList?pageNum=1&pageSize=100
"""

from __future__ import annotations

import logging
from typing import Any

from app.clients import tanyibot
from app.core import cache
from app.core.config import get_env

log = logging.getLogger("aicc_options")

OPTIONS_TTL = 300
PAGE_SIZE = 100
MAX_PAGES = 10


def _paged_post(path: str, body: dict | None = None) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        payload = dict(body or {})
        payload.update({"pageNum": page, "pageSize": PAGE_SIZE})
        data = tanyibot.post(path, payload) or {}
        rows = data.get("content") or []
        items.extend(row for row in rows if isinstance(row, dict))
        if page >= int(data.get("pages") or 1) or not rows:
            break
    return items


def _paged_get(path: str, params: dict | None = None) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        query = dict(params or {})
        query.update({"pageNum": page, "pageSize": PAGE_SIZE})
        data = tanyibot.get(path, params=query) or {}
        rows = data.get("content") or []
        items.extend(row for row in rows if isinstance(row, dict))
        if page >= int(data.get("pages") or 1) or not rows:
            break
    return items


def _options(key: str, loader) -> list[dict]:
    try:
        data, _hit = cache.get_or_load(key, loader, OPTIONS_TTL)
        return data or []
    except Exception as exc:  # 外部接口异常时降级为手填，不阻断发卡
        log.warning("拉取选项失败 %s：%s", key, exc)
        return []


def phone_lines() -> dict[str, list[dict]]:
    """按 phoneType 分组的可用线路。"""
    try:
        data, _hit = cache.get_or_load(
            "aicc:opts:lines",
            lambda: tanyibot.get("/apiEngine/tenantPhoneNumber/getActive") or {},
            OPTIONS_TTL,
        )
        return data or {}
    except Exception as exc:
        log.warning("拉取线路失败：%s", exc)
        return {}


def dialog_bot_allowlist() -> list[dict]:
    """从 .env 读取允许使用的话术 BOT 白名单。

    格式：AICC_DIALOG_BOTS=123:11月新客回访,456:续费提醒
    id 用于调用一知接口，name 仅用于卡片展示；本函数不请求一知。
    """
    raw = (get_env("AICC_DIALOG_BOTS") or "").strip()
    if not raw:
        return []

    bots: list[dict] = []
    for part in raw.replace("，", ",").replace("：", ":").split(","):
        item = part.strip()
        if not item:
            continue
        flow_id, _, name = item.partition(":")
        flow_id = flow_id.strip()
        if not flow_id.isdigit() or int(flow_id) <= 0:
            log.warning("话术 BOT 配置格式不合法，已跳过：%s", item)
            continue
        bots.append({"id": int(flow_id), "name": name.strip() or f"BOT-{flow_id}"})
    return bots


def dialog_flows() -> list[dict]:
    """话术/BOT 列表：id 即创建任务用的 dialogFlowId。

    已封存：创建任务改为读取 .env 白名单（见 dialog_bot_allowlist），
    本函数保留备查，不再被创建流程调用。
    """
    return _options(
        "aicc:opts:dialog_flows",
        lambda: [
            {
                "id": int(row.get("id") or 0),
                "name": row.get("name") or f"BOT-{row.get('id')}",
                "status": row.get("status") or "",
            }
            for row in _paged_post("/apiDialogFlow/dialogFlow/getDialogFlowInfoList")
            if row.get("id")
        ],
    )


def sms_templates() -> list[dict]:
    return _options(
        "aicc:opts:sms_templates",
        lambda: [
            {
                "id": int(row.get("smsTemplateId") or 0),
                "name": row.get("name") or f"短信模板-{row.get('smsTemplateId')}",
                "status": row.get("approvalStatus") or "",
            }
            for row in _paged_get("/apiEngine/smsTemplate/list")
            if row.get("smsTemplateId")
        ],
    )


def staff_groups() -> list[dict]:
    return _options(
        "aicc:opts:staff_groups",
        lambda: [
            {
                "id": int(row.get("csStaffGroupId") or 0),
                "name": row.get("groupName") or f"坐席组-{row.get('csStaffGroupId')}",
                "status": row.get("enabledStatus") or "",
            }
            for row in _paged_get("/apiEngine/csRobot/staffGroupList")
            if row.get("csStaffGroupId")
        ],
    )


def white_groups() -> list[dict]:
    return _options(
        "aicc:opts:white_groups",
        lambda: [
            {
                "id": int(row.get("customerBlackGroupId") or 0),
                "name": row.get("name") or f"客户分组-{row.get('customerBlackGroupId')}",
                "status": row.get("type") or "",
            }
            for row in _paged_get(
                "/aiccWeb/rcs/customerBlackGroup/selectCustomerBlackGroupPageList"
            )
            if row.get("customerBlackGroupId")
        ],
    )


def task_folders() -> list[dict]:
    try:
        rows = tanyibot.get(
            "/apiEngine/robotCallJobFolder/treeFolder",
            params={"systemType": "CALL_OUT"},
        ) or []
    except Exception as exc:
        log.warning("拉取任务文件夹失败：%s", exc)
        return []
    return _flatten_folders(rows)


def _flatten_folders(rows: list[Any], depth: int = 0) -> list[dict]:
    result: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        folder_id = row.get("folderId") or row.get("id")
        if folder_id:
            name = row.get("name") or row.get("folderName") or f"文件夹-{folder_id}"
            result.append({"id": int(folder_id), "name": f"{'　' * depth}{name}"})
        children = row.get("children") or row.get("subFolders") or []
        if children:
            result.extend(_flatten_folders(children, depth + 1))
    return result


def startable_jobs() -> list[dict]:
    """可启动状态的任务，供启动表单下拉。

    listForCollection 的分页数据在 simplePageInfo 下，不是顶层 content。
    """
    def loader() -> list[dict]:
        jobs: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            data = tanyibot.get(
                "/apiEngine/robotCallJob/listForCollection",
                params={
                    "enableTop": "false",
                    "pageNum": page,
                    "pageSize": PAGE_SIZE,
                    "folderId": "",
                    "name": "",
                    "statusSet": "",
                    "orderType": "DRAG_SORT",
                    "beginCreateDate": "",
                    "endCreateDate": "",
                    "jobIds": "",
                    "systemType": "CALL_OUT",
                },
            ) or {}
            page_info = data.get("simplePageInfo") or {}
            rows = page_info.get("content") or []
            jobs.extend(
                {
                    "id": int(row.get("robotCallJobId") or 0),
                    "name": row.get("name") or f"任务-{row.get('robotCallJobId')}",
                    "status": row.get("status") or "",
                    "dialogFlowName": row.get("dialogFlowName") or "",
                }
                for row in rows
                if str(row.get("status") or "") in ("NOT_STARTED", "PAUSED", "USER_PAUSE", "STOPPED")
                and row.get("robotCallJobId")
            )
            if page >= int(page_info.get("pages") or 1) or not rows:
                break
        return jobs

    return _options("aicc:opts:startable_jobs", loader)
