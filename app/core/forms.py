"""卡片表单流程登记表（进程内）。

一条记录贯穿整条流程：step1 填写 → step2 高级设置 → confirm 确认。
token 一次性使用（confirm 阶段），已提交的卡片再点会得到「已处理」。

进程内存储的理由与 HITL 一致：恢复执行依赖当前进程的 Agent/事件循环，
换进程本来也无法继续；TTL 15 分钟，超时后点击提示过期。
"""

from __future__ import annotations

import threading
import time
import uuid

FORM_TTL_SECONDS = 900

_lock = threading.Lock()
_records: dict[str, dict] = {}


def _purge_locked(now: float) -> None:
    for token in [key for key, item in _records.items() if item["expires_at"] <= now]:
        _records.pop(token, None)


def register(
    *,
    kind: str,
    step: str,
    operator: str,
    chat_id: str,
    message_id: str,
    data: dict | None = None,
) -> str:
    token = uuid.uuid4().hex
    now = time.monotonic()
    with _lock:
        _purge_locked(now)
        _records[token] = {
            "token": token,
            "kind": kind,
            "step": step,
            "operator": operator,
            "chat_id": chat_id,
            "message_id": message_id,
            "data": dict(data or {}),
            "expires_at": now + FORM_TTL_SECONDS,
        }
    return token


def get_for(token: str, operator: str = "") -> tuple[dict | None, str]:
    """读取记录并校验操作人，返回 (记录, 原因)；原因：ok / missing / expired / forbidden。"""
    now = time.monotonic()
    with _lock:
        item = _records.get(token)
        if not item:
            return None, "missing"
        if item["expires_at"] <= now:
            _records.pop(token, None)
            return None, "expired"
        if operator and item["operator"] and operator != item["operator"]:
            return None, "forbidden"
        return dict(item), "ok"


def update(token: str, *, step: str | None = None, data: dict | None = None) -> dict | None:
    with _lock:
        item = _records.get(token)
        if not item:
            return None
        if step is not None:
            item["step"] = step
        if data is not None:
            item["data"] = dict(data)
        return dict(item)


def pop_for(token: str, operator: str = "") -> tuple[dict | None, str]:
    """取出并作废（用于确认阶段，防重复执行）。

    校验与删除必须在同一个锁内完成：先 get_for 再 pop 会让两个并发调用
    都读到 "ok"，从而重复执行创建/启动。
    """
    now = time.monotonic()
    with _lock:
        item = _records.get(token)
        if not item:
            return None, "missing"
        if item["expires_at"] <= now:
            _records.pop(token, None)
            return None, "expired"
        if operator and item["operator"] and operator != item["operator"]:
            return None, "forbidden"
        _records.pop(token, None)
        return dict(item), "ok"
