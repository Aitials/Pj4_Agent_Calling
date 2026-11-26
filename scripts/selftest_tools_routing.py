"""离线自检：按渠道裁剪 Agent 工具集 + 卡片模块绑定（不依赖 LLM / 数据库）。

覆盖：
1. 飞书暴露全部工具；
2. 钉钉 4.3 只暴露通用查询 + 创建任务卡片；
3. 企微保持通用查询，不暴露卡片工具；
4. 任何非飞书渠道都不暴露飞书专属工具；
5. `configure_cards` 代理能在飞书/Fake 模块之间正确切换。

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_tools_routing.py
"""

from __future__ import annotations

import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import app.cards.aicc_task_cards as feishu_cards  # noqa: E402
from app.cards import cards, configure_cards  # noqa: E402
from app.tools import (  # noqa: E402
    ALL_TOOLS,
    DINGTALK_TOOLS,
    FEISHU_ONLY,
    WECOM_TOOLS,
    tools_for,
)

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


def _names(tools: list) -> set[str]:
    return {getattr(tool, "name", "") for tool in tools}


def test_feishu() -> None:
    tools = tools_for("feishu")
    _check("飞书暴露全部工具", len(tools) == len(ALL_TOOLS))
    _check("飞书包含任务状态卡片工具", "query_aicc_task_status" in _names(tools))
    _check("飞书不暴露钉钉待办工具", "create_dingtalk_todo" not in _names(tools))


def test_dingtalk() -> None:
    tools = tools_for("dingtalk")
    names = _names(tools)
    _check("钉钉工具数量正确", len(tools) == len(DINGTALK_TOOLS))
    _check("钉钉暴露创建任务卡片", "open_create_task_form" in names)
    _check("钉钉暴露启动任务卡片", "open_start_task_form" in names)
    _check("钉钉暴露状态查询卡片", "query_aicc_task_status" in names)
    _check("钉钉暴露待办工具", "create_dingtalk_todo" in names)
    _check("钉钉不暴露飞书专属工具", not (names & _names(FEISHU_ONLY)))
    _check(
        "钉钉保留通用查询工具",
        {"query_customer_profile", "query_aicc_dashboard"} <= names,
    )


def test_wecom() -> None:
    tools = tools_for("wecom")
    names = _names(tools)
    _check("企微工具数量正确", len(tools) == len(WECOM_TOOLS))
    _check("企微不暴露卡片工具", not (names & {"open_create_task_form", "open_start_task_form"}))
    _check("企微不暴露飞书专属工具", not (names & _names(FEISHU_ONLY)))


def test_unknown_channel() -> None:
    # 未指定渠道时按全量处理，避免把 CLI / 自检跑成残缺工具集
    _check("空渠道返回全部工具", len(tools_for("")) == len(ALL_TOOLS))
    _check("未知渠道退回通用工具", _names(tools_for("unknown")) == _names(WECOM_TOOLS))


def test_cards_binding() -> None:
    configure_cards(feishu_cards)
    _check("默认绑定飞书卡片", cards.build_result is feishu_cards.build_result)

    class FakeModule:
        @staticmethod
        def build_result(*args, **kwargs):
            return {"fake": True}

    configure_cards(FakeModule)
    _check("代理转发到新模块", cards.build_result() == {"fake": True})

    configure_cards(feishu_cards)
    _check("可切回飞书卡片", cards.build_result is feishu_cards.build_result)


def test_task_tool_gate() -> None:
    """钉钉通道下 open_create_task_form 能通过能力判断并派发 open_create。"""
    import asyncio

    import app.flows.aicc_task_flow as flow
    import app.tools.aicc_task_forms as forms_tool
    from app.channels import (
        register_channel,
        reset_current_message,
        set_current_message,
    )
    from app.channels.base import IncomingMessage

    captured: dict = {}

    class FakeChannel:
        name = "dingtalk"
        supports_cards = True

        def __init__(self, loop) -> None:
            self._loop = loop

        def _chat_id(self, message) -> str:
            return message.conversation_id

    async def fake_handle(
        channel, action, value, form_value, operator, chat_id, message_id
    ) -> None:
        captured.update(
            action=action,
            value=value,
            form_value=form_value,
            operator=operator,
            chat_id=chat_id,
            message_id=message_id,
        )

    async def run() -> str:
        channel = FakeChannel(asyncio.get_running_loop())
        register_channel(channel)
        message = IncomingMessage(
            channel="dingtalk",
            message_id="tool-msg-1",
            user_id="user-tool",
            conversation_id="user-tool",
            text="帮我建个外呼任务",
        )
        token = set_current_message(message)
        old = flow.handle_action
        flow.handle_action = fake_handle
        try:
            result = forms_tool._open("create")
            await asyncio.sleep(0.05)
            return result
        finally:
            flow.handle_action = old
            reset_current_message(token)

    result = asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    _check("钉钉发卡工具返回已发送", "表单卡片已发送" in result)
    _check("工具派发 open_create", captured.get("action") == "open_create")
    _check(
        "工具 operator/chat_id 正确",
        captured.get("operator") == "user-tool"
        and captured.get("chat_id") == "user-tool",
    )


def test_router_card_binding() -> None:
    import asyncio

    import app.cards.aicc_task_cards_dingtalk as dingtalk_cards
    import app.router as router

    class FakeChannel:
        name = "dingtalk"

        def set_form_handler(self, handler) -> None:
            return None

        async def start(self, on_message) -> None:
            return None

    async def fake_init_agent(name: str):
        return None

    async def fake_close_memory() -> None:
        return None

    async def run() -> None:
        old_create = router.create_channel
        old_init = router.init_agent
        old_close = router.memory.close_memory
        router.create_channel = lambda name: FakeChannel()
        router.init_agent = fake_init_agent
        router.memory.close_memory = fake_close_memory
        try:
            await router.run_channel("dingtalk")
        finally:
            router.create_channel = old_create
            router.init_agent = old_init
            router.memory.close_memory = old_close

    asyncio.run(run(), loop_factory=asyncio.SelectorEventLoop)
    _check(
        "run_channel 绑定钉钉卡片模块",
        cards.build_result is dingtalk_cards.build_result,
    )
    configure_cards(feishu_cards)


def main() -> int:
    test_feishu()
    test_dingtalk()
    test_wecom()
    test_unknown_channel()
    test_cards_binding()
    test_task_tool_gate()
    test_router_card_binding()
    print()
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
