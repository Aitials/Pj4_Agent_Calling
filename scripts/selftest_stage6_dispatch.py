"""阶段 6 派单链路离线自检：按通道分发、钉钉待办、失败退避与附件跳过。

不连数据库、不发真实请求；全部通过假对象验证调用参数与状态流转。

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_stage6_dispatch.py
"""

from __future__ import annotations

import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import run_notifier  # noqa: E402
from app.tools import DINGTALK_TOOLS, tools_for  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


class _Patcher:
    """临时替换模块属性，结束后统一还原。"""

    def __init__(self) -> None:
        self._saved: list[tuple[object, str, object]] = []

    def set(self, obj, name: str, value) -> None:
        self._saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self) -> None:
        for obj, name, value in reversed(self._saved):
            setattr(obj, name, value)


def _record(record_id: str) -> dict:
    return {
        "record_id": record_id,
        "company_name": "测试客户",
        "customer_phone": "13800000000",
        "customer_name": "测试客户",
        "supplier_level": "高意向",
        "intent_level_code": "HIGH",
        "analysis_basis": "明确要报价",
        "customer_concern": "价格",
        "call_time": None,
        "duration": 30,
        "record_url": "",
        "asr_transcript": "",
    }


def _notice(**overrides) -> dict:
    notice = {
        "id": 11,
        "trigger_record_id": "rec-1",
        "assignee_open_id": "u-1",
        "assignee_name": "张三",
        "channel": "dingtalk",
        "company_name": "测试客户",
        "trigger_phone": "13800000000",
        "retry_count": 0,
    }
    notice.update(overrides)
    return notice


def test_dispatch_by_channel() -> None:
    p = _Patcher()
    calls: dict = {}
    try:
        p.set(run_notifier, "send_one", lambda notice, retry: calls.setdefault("feishu", notice))
        p.set(run_notifier, "send_dingtalk_one", lambda notice, retry: calls.setdefault("dingtalk", notice))
        run_notifier.send_notice(_notice(), 3)
        run_notifier.send_notice(_notice(channel="feishu"), 3)
    finally:
        p.restore()
    _check("无 channel 默认走飞书", calls.get("feishu", {}).get("channel") == "feishu")
    _check("dingtalk 记录走钉钉分支", calls.get("dingtalk", {}).get("channel") == "dingtalk")


def test_dingtalk_success() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))
        p.set(run_notifier.dingtalk_api, "union_id", lambda uid: "union-1")
        p.set(
            run_notifier.dingtalk_api,
            "create_todo",
            lambda union, subject, **kw: cap.update(
                union=union, subject=subject, kwargs=kw
            )
            or {"id": "todo-1", "url": "", "duplicated": False},
        )
        p.set(
            run_notifier.dingtalk_api,
            "send_bot_message",
            lambda ids, text, markdown=False: cap.update(
                ids=ids, text=text, markdown=markdown
            ),
        )
        p.set(
            run_notifier.dispatch_repo,
            "mark_sent",
            lambda *args, **kw: cap.update(mark_sent=(args, kw)),
        )
        ok = run_notifier.send_notice(_notice(), 3)
    finally:
        p.restore()

    _check("钉钉基础通知发送成功", ok is True)
    _check("使用销售 userId 换 unionId", cap.get("union") == "union-1")
    _check("待办 sourceId 按通知固定", cap.get("kwargs", {}).get("source_id") == "notice-11")
    _check("私聊使用 markdown", cap.get("markdown") is True and cap.get("ids") == ["u-1"])
    _check("mark_sent 落 todo_id", cap.get("mark_sent", ((), {}))[1].get("todo_id") == "todo-1")


def test_dingtalk_existing_todo_skips_create() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))

        def _boom(*args, **kwargs):
            raise AssertionError("已有 todo_id 时不应再建待办")

        p.set(run_notifier.dingtalk_api, "create_todo", _boom)
        p.set(
            run_notifier.dingtalk_api,
            "send_bot_message",
            lambda ids, text, markdown=False: cap.update(sent=True),
        )
        p.set(
            run_notifier.dispatch_repo,
            "mark_sent",
            lambda *args, **kw: cap.update(todo_id=kw.get("todo_id")),
        )
        ok = run_notifier.send_notice(_notice(todo_id="todo-existing"), 3)
    finally:
        p.restore()
    _check("已有待办时跳过创建", ok is True and cap.get("sent") is True)
    _check("待办 ID 原样回写", cap.get("todo_id") == "todo-existing")


def test_dingtalk_400001_treated_as_success() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))
        p.set(run_notifier.dingtalk_api, "union_id", lambda uid: "union-1")
        p.set(
            run_notifier.dingtalk_api,
            "create_todo",
            lambda *args, **kwargs: {"id": "", "url": "", "duplicated": True},
        )
        p.set(
            run_notifier.dingtalk_api,
            "send_bot_message",
            lambda ids, text, markdown=False: cap.update(sent=True),
        )
        p.set(run_notifier.dispatch_repo, "mark_sent", lambda *args, **kw: cap.update(sent_mark=True))
        p.set(
            run_notifier.dispatch_repo,
            "mark_failed",
            lambda *args, **kw: cap.update(failed=True),
        )
        ok = run_notifier.send_notice(_notice(), 3)
    finally:
        p.restore()
    _check("400001 按成功处理", ok is True and cap.get("sent") is True)
    _check("400001 不写失败状态", not cap.get("failed"))


def test_dingtalk_quota_not_retried() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))
        p.set(run_notifier.dingtalk_api, "union_id", lambda uid: "union-1")

        def _quota(*args, **kwargs):
            raise run_notifier.dingtalk_api.TodoQuotaExceeded("当日已达上限")

        p.set(run_notifier.dingtalk_api, "create_todo", _quota)
        p.set(
            run_notifier.dispatch_repo,
            "mark_failed",
            lambda *args, **kw: cap.update(args=args, kw=kw),
        )
        ok = run_notifier.send_notice(_notice(), 3)
    finally:
        p.restore()
    _check("400002 不重试", ok is False)
    _check("400002 直接置 FAILED", cap.get("kw", {}).get("next_retry_at") is None)


def test_dingtalk_generic_failure_retries() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))
        p.set(run_notifier.dingtalk_api, "union_id", lambda uid: "")

        def _fail(*args, **kwargs):
            raise run_notifier.dingtalk_api.TodoError("网络抖动")

        p.set(run_notifier.dingtalk_api, "create_todo", _fail)
        p.set(
            run_notifier.dispatch_repo,
            "mark_failed",
            lambda *args, **kw: cap.update(args=args, kw=kw),
        )
        ok = run_notifier.send_notice(_notice(), 3)
    finally:
        p.restore()
    _check("unionId 失败按可重试处理", ok is False)
    _check("首次失败回 PENDING 并退避", cap.get("kw", {}).get("next_retry_at") is not None)


def test_dingtalk_analysis() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(run_notifier.call_record_repo, "get_call_record", lambda rid: _record(rid))
        p.set(
            run_notifier.analysis_repo,
            "get_current",
            lambda rid: {"id": 5, "intent_level": "HIGH", "reason": "要报价", "next_action": "回电"},
        )
        p.set(
            run_notifier.dingtalk_api,
            "send_bot_message",
            lambda ids, text, markdown=False: cap.update(text=text),
        )
        p.set(run_notifier.dispatch_repo, "mark_analysis_sent", lambda nid: cap.update(sent=nid))
        p.set(
            run_notifier.dispatch_repo,
            "mark_attachment_skipped",
            lambda nid, reason="": cap.update(skipped=(nid, reason)),
        )
        analysis_ok = run_notifier._send_dingtalk_analysis(_notice(), 3)
    finally:
        p.restore()
    _check("钉钉分析通知发送成功", analysis_ok is True and cap.get("sent") == 11)
    _check("钉钉附件直接跳过", cap.get("skipped", ("", ""))[0] == 11)
    _check("分析文案包含 AI 分析", "AI 分析" in str(cap.get("text")))


def test_attachment_guard() -> None:
    p = _Patcher()
    cap: dict = {}
    try:
        p.set(
            run_notifier.dispatch_repo,
            "mark_attachment_skipped",
            lambda nid, reason="": cap.update(nid=nid, reason=reason),
        )
        ok = run_notifier.attach_brief_one(_notice(), 3)
    finally:
        p.restore()
    _check("钉钉附件链路直接返回成功", ok is True)
    _check("附件状态标记 SKIPPED", "附件" in str(cap.get("reason")))


def test_tool_gate() -> None:
    names = {getattr(tool, "name", "") for tool in tools_for("dingtalk")}
    _check("钉钉工具集包含待办工具", "create_dingtalk_todo" in names)
    _check("钉钉工具数量与常量一致", len(tools_for("dingtalk")) == len(DINGTALK_TOOLS))
    _check(
        "飞书不暴露钉钉待办工具",
        "create_dingtalk_todo" not in {getattr(tool, "name", "") for tool in tools_for("feishu")},
    )


def main() -> int:
    test_dispatch_by_channel()
    test_dingtalk_success()
    test_dingtalk_existing_todo_skips_create()
    test_dingtalk_400001_treated_as_success()
    test_dingtalk_quota_not_retried()
    test_dingtalk_generic_failure_retries()
    test_dingtalk_analysis()
    test_attachment_guard()
    test_tool_gate()
    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print("=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
