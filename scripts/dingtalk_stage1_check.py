"""钉钉渠道 · 阶段 1 冒烟测试。

覆盖计划书阶段 1 的四项验收：
1. Stream 连接并认证成功（--stream 模式下连接即认证）
2. 单聊发消息 → 收到回调，打印明文 senderStaffId
3. 主动推送单聊消息（oToMessages/batchSend）可达
4. 主动推送群聊消息（groupMessages/send）可达（需先把机器人拉进群）

用法：
    python scripts/dingtalk_stage1_check.py check
    python scripts/dingtalk_stage1_check.py token
    python scripts/dingtalk_stage1_check.py push  --user-id <userId> [--text 文本]
    python scripts/dingtalk_stage1_check.py group --conversation-id <openConversationId> [--text 文本]
    python scripts/dingtalk_stage1_check.py stream [--seconds 120]
    python scripts/dingtalk_stage1_check.py replay [--file 回调.json]
    python scripts/dingtalk_stage1_check.py card --user-id <userId> [--llm] [--interval 0.6]
    python scripts/dingtalk_stage1_check.py cardcb --user-id <userId> [--seconds 180]
    python scripts/dingtalk_stage1_check.py aicard --user-id <userId> [--seconds 180]
    python scripts/dingtalk_stage1_check.py aicard-form --user-id <userId> [--seconds 180]
    python scripts/dingtalk_stage1_check.py tplform --user-id <userId> [--seconds 180]
    python scripts/dingtalk_stage1_check.py sendcard --user-id <userId> [--kind form|buttons]
    python scripts/dingtalk_stage1_check.py cardflow --user-id <userId> [--kind form|buttons]
    python scripts/dingtalk_stage1_check.py openform --user-id <userId>
    python scripts/dingtalk_stage1_check.py createflow --user-id <userId>
    python scripts/dingtalk_stage1_check.py startflow --user-id <userId> [--real]
    python scripts/dingtalk_stage1_check.py statusflow --user-id <userId> [--keyword 名称]

注意：
- 同一个应用只允许一条 Stream 长连接，跑 --stream 前先停掉 run_dingtalk.py；
- 主动推送走 OpenAPI，必须先在开发者后台配置服务端出口 IP 白名单。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as metadata
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import dingtalk_stream  # noqa: E402,F401  SDK 版本自检用
from dingtalk_stream import (  # noqa: E402
    AckMessage,
    CallbackHandler,
    CallbackMessage,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
    EventHandler,
    EventMessage,
)
from dingtalk_stream.card_callback import (  # noqa: E402
    Card_Callback_Router_Topic,
    CardCallbackMessage,
)
from dingtalk_stream.card_instance import AIMarkdownCardInstance  # noqa: E402

from app.agents.base_agent import BaseAgent  # noqa: E402
from app.clients import dingtalk_api  # noqa: E402
from app.core.config import (  # noqa: E402
    DINGTALK_AGENT_ID,
    DINGTALK_CARD_TEMPLATE_FORM,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
    DINGTALK_CORP_ID,
    DINGTALK_ROBOT_CODE,
    Settings,
)

log = logging.getLogger("dingtalk_check")

DEFAULT_PUSH_TEXT = "【钉钉阶段1冒烟】主动推送测试"


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "未安装"


def cmd_check() -> int:
    print("== 环境 ==")
    print(f"Python           : {sys.version.split()[0]}")
    print(f"dingtalk-stream  : {_version('dingtalk-stream')}")
    print(f"alibabacloud-钉钉 : {_version('alibabacloud-dingtalk')}")
    print(f"消息 topic        : {ChatbotMessage.TOPIC}")
    print("\n== 配置（.env）==")
    print(f"DINGTALK_CLIENT_ID     : {dingtalk_api.mask_secret(DINGTALK_CLIENT_ID) or '（空）'}")
    print(f"DINGTALK_CLIENT_SECRET : {dingtalk_api.mask_secret(DINGTALK_CLIENT_SECRET) or '（空）'}")
    print(f"DINGTALK_CORP_ID       : {DINGTALK_CORP_ID or '（空，本期可不填）'}")
    print(f"DINGTALK_ROBOT_CODE    : {DINGTALK_ROBOT_CODE or '（空，主动推送需要）'}")
    print(f"DINGTALK_AGENT_ID      : {DINGTALK_AGENT_ID or '（空，预留）'}")
    print(f"DINGTALK_CARD_TEMPLATE_FORM : {DINGTALK_CARD_TEMPLATE_FORM}")
    if DINGTALK_CARD_TEMPLATE_FORM == dingtalk_api.PUBLIC_FORM_TEMPLATE_ID:
        print("  ⚠️ 仍是官方公开测试模板，生产必须导入自有模板并替换")

    print("\n== 结论 ==")
    if dingtalk_api.is_configured():
        print("凭证已配置，可继续执行 token / push / group / stream")
        return 0
    print("缺少 Client ID / Secret，请先在 .env 中填写后再测")
    return 1


def cmd_token() -> int:
    token = dingtalk_api.access_token()
    if not token:
        print("accessToken 获取失败：检查 Client ID / Secret、应用是否发布、出口 IP 白名单")
        return 1
    print(f"accessToken 获取成功：{dingtalk_api.mask_secret(token)}（有效期约 7200 秒）")
    return 0


def cmd_push(user_id: str, text: str) -> int:
    text = f"{text} {time.strftime('%H:%M:%S')}"
    try:
        dingtalk_api.send_bot_message([user_id], text)
    except Exception as exc:  # noqa: BLE001
        print(f"单聊主动推送失败：{exc}")
        return 1
    print(f"单聊主动推送成功：user={user_id} text={text}")
    return 0


def cmd_group(conversation_id: str, text: str) -> int:
    text = f"{text} {time.strftime('%H:%M:%S')}"
    try:
        dingtalk_api.send_group_message(conversation_id, text)
    except Exception as exc:  # noqa: BLE001
        print(f"群聊主动推送失败：{exc}")
        return 1
    print(f"群聊主动推送成功：conversation={conversation_id} text={text}")
    return 0


class _FakeCallback:
    """replay 用的回调壳，形状与 SDK 的 CallbackMessage 一致（只用到 .data）。"""

    def __init__(self, data: dict) -> None:
        self.data = data


# 与真实单聊回调同构；senderStaffId 为占位值，不会触达真实用户
SAMPLE_CALLBACK = {
    "senderPlatform": "Win",
    "conversationId": "cid-replay",
    "chatbotCorpId": "corp-replay",
    "msgId": "replay-msg-1",
    "senderNick": "重放测试",
    "senderStaffId": "replay-user",
    "conversationType": "1",
    "senderId": "sender-replay",
    "text": {"content": "重放测试"},
    "robotCode": "robot-replay",
    "msgtype": "text",
}


async def _run_replay(data: dict) -> int:
    """同一回调帧连投两次，验证 msgId 去重（不进 Stream、不发消息）。"""
    from app.channels.dingtalk import DingtalkChannel

    channel = DingtalkChannel()
    received: list = []

    async def handler(message) -> None:
        received.append(message)

    channel._handler = handler  # 冒烟脚本直接注入，避免启动真实 Stream

    await channel.on_callback(_FakeCallback(data))
    await asyncio.sleep(0.05)
    first = len(received)

    await channel.on_callback(_FakeCallback(data))
    await asyncio.sleep(0.05)
    second = len(received)

    print(f"第一次投递：{'进入 Agent' if first == 1 else '未进入 Agent'}（handler 调用 {first} 次）")
    print(f"第二次投递：{'被去重' if second == 1 else '重复进入 Agent'}（累计 {second} 次）")
    return 0 if first == 1 and second == 1 else 1


CARD_PROMPT = "请完整背诵《出师表》全文，逐段输出，不要省略，不要解释，不要加标题。"
CARD_PIECES = [
    "【AI 卡片流式冒烟】",
    "这是静态分段推送，",
    "用于验证卡片能力与刷新频率。\n\n",
    "1. 建卡成功\n",
    "2. 流式更新中\n",
    "3. 准备收尾",
]


def _fake_message(user_id: str) -> ChatbotMessage:
    """构造一个单聊消息壳，仅用于卡片投放（不经过 Stream）。"""
    msg = ChatbotMessage()
    msg.sender_staff_id = user_id
    msg.sender_nick = "卡片冒烟"
    msg.sender_id = "selftest-sender"
    msg.sender_corp_id = "selftest-corp"
    msg.conversation_id = "cid-selftest"
    msg.conversation_type = "1"
    msg.message_id = f"selftest-card-{int(time.time())}"
    msg.chatbot_user_id = "selftest-bot"
    return msg


async def _run_card(user_id: str, use_llm: bool, interval: float) -> int:
    """发一张 AI 卡片验证流式输出；--llm 时用真实模型生成《出师表》。"""
    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    card = AIMarkdownCardInstance(client, _fake_message(user_id))
    card.set_title_and_logo("AI 助手 · 流式冒烟", "")

    started = time.perf_counter()
    await asyncio.to_thread(card.ai_start)
    if not card.card_instance_id:
        print("建卡失败：检查卡片能力、模板与出口 IP 白名单")
        return 1
    print(f"建卡+投放成功：{time.perf_counter() - started:.2f}s")

    answer = ""
    updates = 0
    latencies: list[float] = []
    pushed_chars = 0
    last_push = time.perf_counter()

    if use_llm:
        settings = Settings.load()
        agent = BaseAgent(settings)
        print(f"LLM: {settings.llm_model}")
        async for delta in agent.astream(CARD_PROMPT):
            answer += delta
            now = time.perf_counter()
            if now - last_push >= interval and len(answer) - pushed_chars >= 20:
                t1 = time.perf_counter()
                await asyncio.to_thread(card.ai_streaming, answer)
                latencies.append(time.perf_counter() - t1)
                pushed_chars = len(answer)
                updates += 1
                last_push = time.perf_counter()
    else:
        for piece in CARD_PIECES:
            answer += piece
            t1 = time.perf_counter()
            await asyncio.to_thread(card.ai_streaming, answer)
            latencies.append(time.perf_counter() - t1)
            updates += 1
            await asyncio.sleep(interval)

    # 收尾用「全量 + finalize」再推一次：正文只由流式通道渲染，
    # 只调 ai_finish 会让卡片正文停留在最后一次快照上
    await asyncio.to_thread(card.ai_streaming_final, answer)
    await asyncio.to_thread(card.ai_finish, answer)
    total = time.perf_counter() - started
    avg = sum(latencies) / len(latencies) if latencies else 0.0
    print("\n===== 卡片流式结果 =====")
    print(f"内容长度: {len(answer)} 字")
    print(f"更新次数: {updates}")
    print(f"单次推流耗时: avg={avg:.2f}s max={max(latencies, default=0):.2f}s")
    print(f"整体耗时: {total:.2f}s")
    print("请到钉钉单聊查看卡片是否逐段刷新、最终为完成态")
    return 0


# ---------------------------------------------------------------------------
# 阶段 4 探针：发一张带按钮的卡片，打印真实「卡片按钮回调」帧
# ---------------------------------------------------------------------------

CARD_PROBE_MARKDOWN = (
    "**钉钉互动卡片回调探针**\n\n"
    "请点击下面任意按钮，脚本会打印完整回调帧。\n"
    "按钮 id 里已带上 token，用于确认自定义数据能否随回调返回。"
)

# AI 卡片模板支持的按钮结构；request=True 表示点击后回传请求（不是打开链接）
CARD_PROBE_BUTTONS = [
    {
        "text": "确认执行",
        "color": "blue",
        "id": "task_create_confirm|probe-token-001",
        "request": True,
    },
    {
        "text": "取消",
        "color": "blue",
        "id": "task_form_cancel|probe-token-001",
        "request": True,
    },
]

# ===== A2UI（JSON 构建卡片）探针 =====
A2UI_API_BASE = "https://api.dingtalk.com"
A2UI_CATALOG = "https://dingtalk.com/card/a2ui/catalogs/public/catalog.json"
A2UI_TIMEOUT_SECONDS = 10.0


def _a2ui_call(method: str, path: str, payload: dict) -> tuple[int, dict]:
    """直调 OpenAPI 并打印完整响应；401 时刷新 token 重试一次。"""
    status, data = 0, {}
    for attempt in range(2):
        token = dingtalk_api.access_token(force=attempt > 0)
        if not token:
            print("accessToken 获取失败：检查凭证与出口 IP 白名单")
            return 0, {}
        try:
            with httpx.Client(
                base_url=A2UI_API_BASE,
                timeout=A2UI_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = client.request(
                    method,
                    path,
                    headers={"x-acs-dingtalk-access-token": token},
                    json=payload,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"{method} {path} 请求异常：{exc}")
            return 0, {}

        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {}
        status, data = response.status_code, body if isinstance(body, dict) else {}
        if status == 401 and attempt == 0:
            print("accessToken 失效，刷新后重试")
            continue
        break

    print(f"\n{method} {path} -> HTTP {status}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])
    if status >= 400:
        text = json.dumps(data, ensure_ascii=False)
        if "requiredScopes" in text:
            print("响应里包含 requiredScopes：按其中权限点到开发者后台申请后重试")
    return status, data


def _a2ui_static_card() -> tuple[str, dict, list[dict]]:
    """静态 A2UI 卡：两个请求按钮，context 只带隐藏 token。"""
    surface = "probe-static"
    data = {
        "title": "A2UI 静态卡探针",
        "status": "尚未点击",
        "hint": "点击下面按钮，脚本会打印回调 topic 与完整帧。",
        "meta": {"token": "probe-token-001"},
    }
    components = [
        {
            "id": "root",
            "component": "Column",
            "children": ["title", "hint", "status", "actions"],
            "gap": 8,
        },
        {"id": "title", "component": "Text", "text": {"path": "/title"}, "bold": True},
        {"id": "hint", "component": "Text", "text": {"path": "/hint"}, "maxLine": 3},
        {
            "id": "status",
            "component": "Text",
            "text": {"path": "/status"},
            "variant": "caption",
        },
        {
            "id": "actions",
            "component": "Row",
            "justify": "end",
            "children": ["btn-confirm", "btn-cancel"],
        },
        {
            "id": "btn-confirm",
            "component": "Button",
            "child": "btn-confirm-label",
            "variant": "primary",
            "action": {
                "event": {
                    "name": "task_create_confirm",
                    "context": {"token": {"path": "/meta/token"}},
                }
            },
        },
        {"id": "btn-confirm-label", "component": "Text", "text": "确认执行"},
        {
            "id": "btn-cancel",
            "component": "Button",
            "child": "btn-cancel-label",
            "action": {
                "event": {
                    "name": "task_form_cancel",
                    "context": {"token": {"path": "/meta/token"}},
                }
            },
        },
        {"id": "btn-cancel-label", "component": "Text", "text": "取消"},
    ]
    return surface, data, components


def _a2ui_form_card() -> tuple[str, dict, list[dict]]:
    """表单 A2UI 卡：输入控件 + 提交按钮，context 带 token 与整份 form。"""
    surface = "probe-form"
    data = {
        "title": "A2UI 表单卡探针",
        "status": "填写后点击提交",
        "intro": "填完点提交，脚本会打印回调帧里的 `params.form`。",
        "meta": {"token": "probe-token-form"},
        "form": {"name": "", "lines": [], "count": 1, "agree": False, "note": ""},
    }
    components = [
        {
            "id": "root",
            "component": "Column",
            "children": [
                "title",
                "intro",
                "f-name",
                "lines-title",
                "f-lines",
                "count-title",
                "f-count",
                "agrees",
                "note-title",
                "f-note",
                "actions",
                "status",
            ],
            "gap": 8,
        },
        {"id": "title", "component": "Text", "text": {"path": "/title"}, "bold": True},
        {"id": "intro", "component": "Text", "text": {"path": "/intro"}, "maxLine": 3},
        {
            "id": "f-name",
            "component": "TextField",
            "label": "任务名称",
            "placeholder": "例如：11月新客回访",
            "value": {"path": "/form/name"},
        },
        {
            "id": "lines-title",
            "component": "Text",
            "text": "主叫线路（可多选）",
            "variant": "caption",
        },
        {
            "id": "f-lines",
            "component": "ChoicePicker",
            "variant": "multipleSelection",
            "displayStyle": "checkbox",
            "options": [
                {"label": "线路 A", "value": "line-a"},
                {"label": "线路 B", "value": "line-b"},
            ],
            "value": {"path": "/form/lines"},
        },
        {"id": "count-title", "component": "Text", "text": "并发数", "variant": "caption"},
        {
            "id": "f-count",
            "component": "NumberInput",
            "label": "并发数",
            "min": 1,
            "value": {"path": "/form/count"},
        },
        {
            "id": "agrees",
            "component": "CheckBox",
            "label": "我已确认参数无误",
            "value": {"path": "/form/agree"},
        },
        {"id": "note-title", "component": "Text", "text": "备注（可空）", "variant": "caption"},
        {
            "id": "f-note",
            "component": "TextField",
            "label": "备注",
            "variant": "longText",
            "value": {"path": "/form/note"},
        },
        {
            "id": "actions",
            "component": "Row",
            "justify": "end",
            "children": ["btn-submit"],
        },
        {
            "id": "btn-submit",
            "component": "Button",
            "child": "btn-submit-label",
            "variant": "primary",
            "checks": [
                {
                    "condition": {
                        "call": "required",
                        "args": {"value": {"path": "/form/name"}},
                    },
                    "message": "请填写任务名称",
                },
                {
                    "condition": {
                        "call": "required",
                        "args": {"value": {"path": "/form/lines"}},
                    },
                    "message": "请至少选择一条线路",
                },
            ],
            "action": {
                "event": {
                    "name": "task_create_step1",
                    "context": {
                        "token": {"path": "/meta/token"},
                        "form": {"path": "/form"},
                    },
                }
            },
        },
        {"id": "btn-submit-label", "component": "Text", "text": "提交"},
        {
            "id": "status",
            "component": "Text",
            "text": {"path": "/status"},
            "variant": "caption",
        },
    ]
    return surface, data, components


def _send_a2ui_card(
    user_id: str,
    space_type: str,
    surface: str,
    data: dict,
    components: list[dict],
    summary: str,
) -> str:
    """创建并投放 A2UI 卡片，返回 outTrackId；失败返回空串。"""
    out_track_id = f"probe-{surface}-{int(time.time() * 1000)}"
    messages = [
        {
            "version": "v1.0",
            "createSurface": {"surfaceId": surface, "catalogId": A2UI_CATALOG},
        },
        {
            "version": "v1.0",
            "updateDataModel": {"surfaceId": surface, "path": "/", "value": data},
        },
        {
            "version": "v1.0",
            "updateComponents": {"surfaceId": surface, "components": components},
        },
    ]
    payload = {
        "outTrackId": out_track_id,
        "openSpaceId": f"dtv1.card//{space_type}.{user_id}",
        "protocolVersion": "1.0",
        "flowStatus": "PROCESSING",
        "a2uiMessages": [json.dumps(item, ensure_ascii=False) for item in messages],
        "a2uiAnnotations": [
            {"surfaceId": surface, "componentId": "title", "type": "artifact"}
        ],
        "imRobotOpenDeliverModel": {"robotCode": DINGTALK_ROBOT_CODE},
        "summary": summary,
        "userIdType": 1,
        "callbackType": "STREAM",
    }
    status, body = _a2ui_call(
        "POST", "/v1.0/card/ai/instances/createAndDeliver", payload
    )
    result = body.get("result") if isinstance(body, dict) else None
    deliver = (result or {}).get("deliverResults") or []
    ok = (
        status < 400
        and bool(body.get("success"))
        and all(item.get("success") for item in deliver if isinstance(item, dict))
    )
    if ok:
        print(f"卡片已投放：outTrackId={out_track_id}")
        return out_track_id

    print(
        "卡片投放失败：检查 AICard.Instance.Write 权限、DINGTALK_ROBOT_CODE、"
        "openSpaceId 与 userIdType"
    )
    return ""


def _update_a2ui_card(
    out_track_id: str, surface: str, path: str, value, flow_status: str = "PROCESSING"
) -> bool:
    """更新 A2UI 卡片的某个数据路径（验证 update 接口）。"""
    message = {
        "version": "v1.0",
        "updateDataModel": {"surfaceId": surface, "path": path, "value": value},
    }
    payload = {
        "outTrackId": out_track_id,
        "outUpdateId": f"{out_track_id}-{int(time.time() * 1000)}",
        "flowStatus": flow_status,
        "a2uiMessages": [json.dumps(message, ensure_ascii=False)],
    }
    status, body = _a2ui_call("PUT", "/v1.0/card/ai/instances/update", payload)
    return status < 400 and bool(body.get("success"))



class _CardProbeHandler(CallbackHandler):
    """卡片回调探针：先打印原始帧并立即 ACK，换卡放到后台任务。

    官方约束：卡片回调 2 秒超时，且「请勿在回调过程中调用更新接口」，
    所以这里只做打印 + 登记，更新一律用 create_task 异步执行。
    """

    def __init__(self) -> None:
        super().__init__()
        self.received = asyncio.Event()
        self.last_update = ""
        # outTrackId -> surfaceId，用于区分 A2UI 卡与模板卡
        self.a2ui_surfaces: dict[str, str] = {}
        # 模板表单卡：回调时直接返回「已提交」响应，让表单禁用
        self.form_cards: set[str] = set()

    async def process(self, callback: CallbackMessage):
        data = callback.data if isinstance(callback.data, dict) else {}
        topic = getattr(getattr(callback, "headers", None), "topic", "")
        print(f"\n== 卡片回调 topic={topic!r} ==")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])

        try:
            msg = CardCallbackMessage.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            print(f"\nSDK 解析失败（原始帧仍然有效，按上面内容人工核对）：{exc}")
            self.received.set()
            return AckMessage.STATUS_OK, "OK"

        content = msg.content if isinstance(msg.content, dict) else {}
        private = content.get("cardPrivateData")
        action_ids = content.get("actionIds")
        params = content.get("params")
        if isinstance(private, dict):
            action_ids = private.get("actionIds", action_ids)
            params = private.get("params", params)

        print("\n== SDK 解析结果 ==")
        print(f"userId          : {msg.user_id!r}  ← 期望是点击人的明文 userId")
        print(f"outTrackId      : {msg.card_instance_id!r}")
        print(f"content keys    : {list(content.keys())}")
        print(f"actionIds       : {action_ids}")
        print(f"params          : {json.dumps(params, ensure_ascii=False)[:2000]}")
        if isinstance(params, dict):
            for key in ("action", "token", "form", "event", "name"):
                if key in params:
                    print(
                        f"params[{key}]     : "
                        f"{json.dumps(params[key], ensure_ascii=False)[:1200]}"
                    )
        if isinstance(private, dict):
            print(f"cardPrivateData : {json.dumps(private, ensure_ascii=False)[:1200]}")

        # 模板表单卡：先 ACK，并在响应里把表单置为已提交（官方示例写法）
        if msg.card_instance_id in self.form_cards and isinstance(params, dict):
            response = _form_submitted_response(params)
            self.last_update = "表单已提交（响应内更新）"
            print("表单响应        : 已返回 form_status=disabled / form_btn_text=已提交")
            self.received.set()
            return AckMessage.STATUS_OK, response

        # 其他卡片：先 ACK，再异步换卡（不阻塞 2 秒窗口）
        if msg.card_instance_id:
            surface = self.a2ui_surfaces.get(msg.card_instance_id)
            asyncio.create_task(
                self._replace_card(msg.card_instance_id, surface)
            )

        self.received.set()
        return AckMessage.STATUS_OK, "OK"

    async def _replace_card(self, card_instance_id: str, surface: str | None) -> None:
        """ACK 之后才执行的换卡动作，结果记到 last_update 供脚本打印。"""
        if surface:
            ok = await asyncio.to_thread(
                _update_a2ui_card,
                card_instance_id,
                surface,
                "/status",
                "已收到点击（探针）",
            )
            self.last_update = "A2UI 更新成功" if ok else "A2UI 更新失败"
            return

        ok = await asyncio.to_thread(
            dingtalk_api.update_card,
            card_instance_id,
            dingtalk_api.ai_card_text_data(
                "互动卡片探针", "已收到按钮回调，已替换为无按钮卡。"
            ),
        )
        self.last_update = "模板卡换卡成功" if ok else "模板卡换卡失败"


async def _run_card_probe(user_id: str, seconds: float) -> int:
    """发一张双按钮卡，等用户点击后打印真实回调帧。"""
    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    handler = _CardProbeHandler()
    client.register_callback_handler(Card_Callback_Router_Topic, handler)

    print(f"订阅卡片回调 topic={Card_Callback_Router_Topic}")
    print("正在建立 Stream 连接（同一应用只允许一条长连接，请先停掉 run_dingtalk.py）…")
    asyncio.create_task(client.start())
    # 建连需要先拿 ticket 再握手，给一点时间；卡片投放本身不依赖 Stream
    await asyncio.sleep(3.0)

    # 复用通道层的可观测卡片，能判断内容推送是否成功
    from app.channels.dingtalk import _ObservableAiCard

    card = _ObservableAiCard(client, _fake_message(user_id))
    card.set_title_and_logo("互动卡片探针", "")
    await asyncio.to_thread(card.ai_start)
    if not card.card_instance_id:
        print("建卡失败：检查卡片能力、模板与出口 IP 白名单")
        return 1

    await asyncio.to_thread(
        card.ai_streaming_final, CARD_PROBE_MARKDOWN
    )
    await asyncio.to_thread(
        card.ai_finish, CARD_PROBE_MARKDOWN, CARD_PROBE_BUTTONS
    )
    print(
        f"卡片已发送：user={user_id} outTrackId={card.card_instance_id} "
        f"按钮内容推送={'成功' if card.last_update_ok else '失败'}"
    )
    print(f"请在钉钉单聊里点击卡片上的任意按钮，等待回调（最多 {seconds:.0f} 秒）…")

    try:
        await asyncio.wait_for(handler.received.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print(
            "超时未收到卡片回调：确认应用已发布、卡片能力已开通，"
            "并且点击的是本应用发出的卡片"
        )
        return 1

    # 等异步换卡完成后再退出，避免 os._exit 杀掉后台任务
    await asyncio.sleep(2.5)
    print(f"卡片更新结果   : {handler.last_update or '未完成（看上方日志）'}")
    print("\n== 观察要点（把上面的原始帧发回来即可定卡片写法）==")
    print("1. content 里是否有 actionIds / params，按钮 id 是否原样回传（token 能否携带）")
    print("2. userId 是否为点击人明文 userId（forms 的 operator 校验依赖它）")
    print("3. outTrackId 是否等于本轮发出的卡片实例 ID")
    print("4. 卡片是否已被成功替换成「无按钮」状态（防重复点击）")
    return 0


async def _run_a2ui_probe(
    user_id: str, space_type: str, seconds: float, form: bool
) -> int:
    """发送 A2UI 静态卡/表单卡，等点击后打印真实回调帧。"""
    if not DINGTALK_ROBOT_CODE:
        print("缺少 DINGTALK_ROBOT_CODE，无法以机器人身份投放卡片")
        return 1

    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    handler = _CardProbeHandler()
    client.register_callback_handler(Card_Callback_Router_Topic, handler)

    print(f"订阅卡片回调 topic={Card_Callback_Router_Topic}")
    print("正在建立 Stream 连接（同一应用只允许一条长连接，请先停掉 run_dingtalk.py）…")
    asyncio.create_task(client.start())
    await asyncio.sleep(3.0)

    surface, data, components = (
        _a2ui_form_card() if form else _a2ui_static_card()
    )
    summary = "A2UI 表单卡探针" if form else "A2UI 静态卡探针"
    out_track_id = await asyncio.to_thread(
        _send_a2ui_card, user_id, space_type, surface, data, components, summary
    )
    if not out_track_id:
        return 1
    handler.a2ui_surfaces[out_track_id] = surface

    if not form:
        # 静态卡：先验证 update 接口，再等点击（官方示例等待 3 秒再更新）
        await asyncio.sleep(3.0)
        updated = await asyncio.to_thread(
            _update_a2ui_card,
            out_track_id,
            surface,
            "/status",
            "update 接口已生效（探针）",
        )
        print(f"update 接口     : {'成功' if updated else '失败（看上方响应）'}")

    print(
        f"卡片已发送：user={user_id} surface={surface} "
        f"outTrackId={out_track_id} space=dtv1.card//{space_type}.{user_id}"
    )
    if form:
        print("请填写表单并点「提交」，脚本会打印回调帧（重点看 params.form）。")
    else:
        print("请点击卡片上的任意按钮，脚本会打印回调帧（重点看 actionIds/params）。")
    print(f"等待回调（最多 {seconds:.0f} 秒）…")

    try:
        await asyncio.wait_for(handler.received.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print(
            "超时未收到卡片回调：确认应用已发布、AICard.Instance.Write 已开通、"
            "点击的是本应用发出的卡片；若 A2UI 回调走独立 topic，把上面的日志发回来"
        )
        return 1

    # 等异步换卡完成后再退出，避免 os._exit 杀掉后台任务
    await asyncio.sleep(2.5)
    print(f"卡片更新结果   : {handler.last_update or '未完成（看上方日志）'}")
    print("\n== 观察要点 ==")
    if form:
        print("1. params 里是否含 form 与 token，form.name/lines/count/agree/note 是否与填写一致")
        print("2. 事件名是否体现为 actionIds 或 params.action/event")
        print("3. userId 是否为点击人明文 staffId")
        print("4. outTrackId 是否等于本轮发送的卡片实例 ID")
    else:
        print("1. actionIds/params 是否能带回 event 名与 token")
        print("2. userId 是否明文、outTrackId 是否一致")
        print("3. 卡片上的 status 是否已被 update 接口改为「update 接口已生效」")
    print("5. topic 是否仍是 /v1.0/card/instances/callback（把原始 topic 发回来）")
    return 0


# 官方文档里的公开表单模板（测试用；生产需导入到自己的应用下）
TEMPLATE_FORM_ID = "280f6d7a-63bc-4905-bf3f-4c6d95e5166b.schema"


def _stringify_card_data(data: dict) -> dict:
    """cardParamMap 只接受字符串：非字符串统一 JSON 序列化。"""
    return {
        key: (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
        for key, value in data.items()
    }


def _form_submitted_response(params: dict) -> dict:
    """按官方示例返回「表单已提交」，让卡片禁用并回显已填值。"""
    form = params.get("form") if isinstance(params.get("form"), dict) else {}
    current = (
        params.get("current_form")
        if isinstance(params.get("current_form"), dict)
        else {}
    )
    for field in current.get("fields") or []:
        if isinstance(field, dict) and field.get("name") in form:
            field["defaultValue"] = form[field["name"]]

    user_private = {
        "form": current,
        "form_status": "disabled",
        "form_btn_text": "已提交",
    }
    return {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
        "userPrivateData": {"cardParamMap": _stringify_card_data(user_private)},
    }


def _template_form_card() -> dict:
    """模板表单卡：字段协议来自官方「表单变量」。"""
    return {
        "title": "模板表单卡探针",
        "form_status": "normal",
        "form_btn_text": "提交",
        "form": {
            "fields": [
                {
                    "name": "name",
                    "label": "任务名称",
                    "type": "TEXT",
                    "required": True,
                    "placeholder": "例如：11月新客回访",
                },
                {
                    "name": "lines",
                    "label": "主叫线路（可多选）",
                    "type": "MULTI_SELECT",
                    "required": True,
                    "options": [
                        {"value": "line-a", "text": "线路 A"},
                        {"value": "line-b", "text": "线路 B"},
                    ],
                },
                {
                    "name": "count",
                    "label": "并发数",
                    "type": "NUMBER",
                    "required": True,
                    "defaultValue": 1,
                },
                {
                    "name": "note",
                    "label": "备注（可空）",
                    "type": "TEXT_AREA",
                },
                {
                    "name": "token",
                    "type": "TEXT",
                    "hidden": True,
                    "defaultValue": "probe-token-tpl",
                },
            ]
        },
    }


async def _run_open_form(user_id: str) -> int:
    """4.3 冒烟：直接走 flow 发第一张创建任务卡（不经过 LLM）。"""
    from app.cards import aicc_task_cards_dingtalk, configure_cards
    from app.channels.dingtalk import DingtalkChannel
    from app.clients import aicc_options
    from app.flows import aicc_task_flow

    configure_cards(aicc_task_cards_dingtalk)
    if not aicc_options.dialog_bot_allowlist():
        print("未配置 AICC_DIALOG_BOTS：open_create_form 会回文本提示，不会发卡")

    channel = DingtalkChannel()
    await aicc_task_flow.open_create_form(
        channel, user_id, user_id, f"openform-{int(time.time())}"
    )
    print("open_create_form 已调用；请查看钉钉单聊")
    return 0


async def _run_card_flow(user_id: str, kind: str, seconds: float) -> int:
    """启动真实通道，验证 4.2 的卡片回调解析与派发。"""
    from app.channels.dingtalk import DingtalkChannel

    channel = DingtalkChannel()
    received = asyncio.Event()
    captured: dict = {}

    async def stub(action, value, form_value, operator, chat_id, message_id):
        captured.update(
            action=action,
            value=value,
            form_value=form_value,
            operator=operator,
            chat_id=chat_id,
            message_id=message_id,
        )
        received.set()

    async def noop(message) -> None:
        return None

    channel.set_form_handler(stub)
    start_task = asyncio.create_task(channel.start(noop))
    for _ in range(50):
        if channel._client is not None:
            break
        await asyncio.sleep(0.1)
    if channel._client is None:
        print("Stream 客户端未就绪：检查凭证与是否已有其他长连接")
        return 1
    # 等建连握手完成
    await asyncio.sleep(3.0)

    if kind == "form":
        card = {
            "kind": "form",
            "title": "4.2 回调底座冒烟",
            "btn_text": "提交",
            "fields": [
                {
                    "name": "name",
                    "label": "任务名称",
                    "type": "TEXT",
                    "required": True,
                    "placeholder": "随便填一个",
                },
                {
                    "name": "lines",
                    "label": "主叫线路（可多选）",
                    "type": "MULTI_SELECT",
                    "required": True,
                    "options": [
                        {"value": "line-a", "text": "线路 A"},
                        {"value": "line-b", "text": "线路 B"},
                    ],
                },
                {
                    "name": "count",
                    "label": "并发数",
                    "type": "NUMBER",
                    "required": True,
                    "defaultValue": 1,
                },
            ],
            "hidden": {
                "token": "flow-token-1",
                "__action": "task_create_step1",
            },
        }
    else:
        card = {
            "kind": "buttons",
            "title": "4.2 按钮回调冒烟",
            "markdown": "点击按钮，脚本会打印解析后的派发参数。",
            "buttons": [
                {
                    "text": "确认执行",
                    "id": "task_create_confirm|flow-token-btn",
                    "color": "blue",
                }
            ],
        }

    out_track_id = await channel.send_card_to_chat(user_id, card)
    print(f"卡片已发送：kind={kind} outTrackId={out_track_id}")
    print(f"等待提交/点击（最多 {seconds:.0f} 秒）…")
    try:
        await asyncio.wait_for(received.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print("超时未收到卡片回调")
        return 1

    await asyncio.sleep(1.0)
    print("\n== 通道派发参数 ==")
    print(json.dumps(captured, ensure_ascii=False, indent=2, default=str))
    if not start_task.done():
        print("卡片回调已解析并派发；通道仍在后台运行，脚本退出时终止。")
    return 0


async def _run_create_flow(user_id: str, seconds: float) -> int:
    """4.4 端到端：step1 → step2 → 确认 → 真实创建任务。"""
    from app.cards import aicc_task_cards_dingtalk, configure_cards
    from app.channels.dingtalk import DingtalkChannel
    from app.clients import aicc_options
    from app.flows import aicc_task_flow

    configure_cards(aicc_task_cards_dingtalk)
    if not aicc_options.dialog_bot_allowlist():
        print("未配置 AICC_DIALOG_BOTS：open_create_form 会回文本提示，不会发卡")

    channel = DingtalkChannel()
    done = asyncio.Event()

    async def handler(action, value, form_value, operator, chat_id, message_id):
        print(f"[flow] action={action} operator={operator}")
        await aicc_task_flow.handle_action(
            channel, action, value, form_value, operator, chat_id, message_id
        )
        if action == "task_create_confirm":
            done.set()

    async def noop(message) -> None:
        return None

    channel.set_form_handler(handler)
    asyncio.create_task(channel.start(noop))
    for _ in range(50):
        if channel._client is not None:
            break
        await asyncio.sleep(0.1)
    if channel._client is None:
        print("Stream 客户端未就绪：检查凭证与是否已有其他长连接")
        return 1
    await asyncio.sleep(3.0)

    await aicc_task_flow.open_create_form(
        channel, user_id, user_id, f"createflow-{int(time.time())}"
    )
    print("已发送第 1 步表单；请依次填写并提交，最后点「确认执行」。")
    print(f"等待流程完成（最多 {seconds:.0f} 秒）…")
    try:
        await asyncio.wait_for(done.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print("超时：创建流程未走到确认")
        return 1

    # 等结果卡发出
    await asyncio.sleep(3.0)
    print("创建流程已走完；请查看钉钉里的结果卡。")
    return 0


async def _run_start_flow(user_id: str, seconds: float, dry_run: bool) -> int:
    """4.5 端到端：启动表单 → 确认 → 启动任务（默认 dry-run）。"""
    from app.cards import aicc_task_cards_dingtalk, configure_cards
    from app.channels.dingtalk import DingtalkChannel
    from app.flows import aicc_task_flow
    from app.tools import aicc_task

    configure_cards(aicc_task_cards_dingtalk)
    channel = DingtalkChannel()
    done = asyncio.Event()

    async def handler(action, value, form_value, operator, chat_id, message_id):
        print(f"[flow] action={action} operator={operator}")
        await aicc_task_flow.handle_action(
            channel, action, value, form_value, operator, chat_id, message_id
        )
        if action == "task_start_confirm":
            done.set()

    async def noop(message) -> None:
        return None

    channel.set_form_handler(handler)
    asyncio.create_task(channel.start(noop))
    for _ in range(50):
        if channel._client is not None:
            break
        await asyncio.sleep(0.1)
    if channel._client is None:
        print("Stream 客户端未就绪：检查凭证与是否已有其他长连接")
        return 1
    await asyncio.sleep(3.0)

    original_start = aicc_task.start_call_task
    if dry_run:
        aicc_task.start_call_task = lambda job_id: (
            f"✅ [dry-run] 已模拟启动任务 {job_id}，未真实调用"
        )
    try:
        await aicc_task_flow.open_start_form(
            channel, user_id, user_id, f"startflow-{int(time.time())}"
        )
        print(
            f"已发送启动表单（{'dry-run' if dry_run else '真实启动'}）；"
            "请选择任务并点「确认执行」。"
        )
        print(f"等待流程完成（最多 {seconds:.0f} 秒）…")
        try:
            await asyncio.wait_for(done.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            print("超时：启动流程未走到确认")
            return 1
        await asyncio.sleep(3.0)
        print(f"启动流程已走完（dry-run={dry_run}）；请查看结果卡。")
        return 0
    finally:
        aicc_task.start_call_task = original_start


async def _run_status_flow(user_id: str, seconds: float, keyword: str = "") -> int:
    """4.6 端到端：筛选 → 列表 → 概况（或按关键词直达）。"""
    from app.cards import aicc_task_cards_dingtalk, configure_cards
    from app.channels.dingtalk import DingtalkChannel
    from app.flows import aicc_task_flow

    configure_cards(aicc_task_cards_dingtalk)
    channel = DingtalkChannel()
    done = asyncio.Event()

    async def handler(action, value, form_value, operator, chat_id, message_id):
        print(f"[flow] action={action} operator={operator}")
        await aicc_task_flow.handle_action(
            channel, action, value, form_value, operator, chat_id, message_id
        )
        if action in ("task_status_pick", "task_status_refresh"):
            done.set()

    async def noop(message) -> None:
        return None

    channel.set_form_handler(handler)
    asyncio.create_task(channel.start(noop))
    for _ in range(50):
        if channel._client is not None:
            break
        await asyncio.sleep(0.1)
    if channel._client is None:
        print("Stream 客户端未就绪：检查凭证与是否已有其他长连接")
        return 1
    await asyncio.sleep(3.0)

    await aicc_task_flow.open_status_form(
        channel, user_id, user_id, f"statusflow-{int(time.time())}", keyword
    )
    if keyword:
        print(f"已按关键词「{keyword}」查询；命中单个任务会直接发概况。")
        await asyncio.sleep(5.0)
        print("关键词查询已发送，请查看钉钉。")
        return 0

    print("已发送筛选表单；请提交筛选，再点某个任务的「查看 #ID」。")
    print(f"等待概况卡（最多 {seconds:.0f} 秒）…")
    try:
        await asyncio.wait_for(done.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print("超时：未走到概况卡")
        return 1
    await asyncio.sleep(3.0)
    print("已收到概况卡；请查看钉钉。")
    return 0


async def _run_sendcard(user_id: str, kind: str) -> int:
    """验证阶段 4.1 的通道发送链路（只发卡，不等待回调）。"""
    from app.channels.dingtalk import DingtalkChannel

    channel = DingtalkChannel()
    if kind == "form":
        card = {
            "kind": "form",
            "title": "4.1 表单卡冒烟",
            "btn_text": "下一步",
            "fields": [
                {
                    "name": "name",
                    "label": "任务名称",
                    "type": "TEXT",
                    "required": True,
                    "placeholder": "例如：11月新客回访",
                },
                {
                    "name": "lines",
                    "label": "主叫线路（可多选）",
                    "type": "MULTI_SELECT",
                    "required": True,
                    "options": [
                        {"value": "line-a", "text": "线路 A"},
                        {"value": "line-b", "text": "线路 B"},
                    ],
                },
                {
                    "name": "count",
                    "label": "并发数",
                    "type": "NUMBER",
                    "required": True,
                    "defaultValue": 1,
                },
            ],
            "hidden": {
                "token": "probe-token-41",
                "__action": "task_create_step1",
            },
        }
    else:
        client = DingTalkStreamClient(
            Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
            logger=logging.getLogger("dingtalk_stream"),
        )
        channel._client = client
        card = {
            "kind": "buttons",
            "title": "4.1 按钮卡冒烟",
            "markdown": (
                "**4.1 按钮卡冒烟**\n\n"
                "这是一张通过 `send_card_to_chat` 发出的 AI Markdown 卡。"
            ),
            "buttons": [
                {
                    "text": "确认执行",
                    "id": "task_create_confirm|probe-token-41",
                    "color": "blue",
                },
                {
                    "text": "取消",
                    "id": "task_form_cancel|probe-token-41",
                    "color": "blue",
                },
            ],
        }

    out_track_id = await channel.send_card_to_chat(user_id, card)
    print(f"send_card_to_chat 成功：kind={kind} outTrackId={out_track_id}")
    print("请到钉钉单聊查看卡片；本步骤不等待回调。")
    return 0


async def _run_template_form_probe(user_id: str, seconds: float) -> int:
    """用官方公开表单模板发卡，验证「表单值一次回传」链路（不需新权限）。"""
    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    handler = _CardProbeHandler()
    client.register_callback_handler(Card_Callback_Router_Topic, handler)

    print(f"订阅卡片回调 topic={Card_Callback_Router_Topic}")
    print("正在建立 Stream 连接（同一应用只允许一条长连接，请先停掉 run_dingtalk.py）…")
    asyncio.create_task(client.start())
    await asyncio.sleep(3.0)

    replier = dingtalk_stream.CardReplier(client, _fake_message(user_id))
    card_instance_id = await asyncio.to_thread(
        replier.create_and_send_card,
        TEMPLATE_FORM_ID,
        _stringify_card_data(_template_form_card()),
    )
    if not card_instance_id:
        print("模板表单卡发送失败：检查卡片能力与出口 IP 白名单")
        return 1

    handler.form_cards.add(card_instance_id)
    print(f"模板表单卡已发送：outTrackId={card_instance_id} template={TEMPLATE_FORM_ID}")
    print("请填写表单并点「提交」，脚本会打印回调帧（重点看 params.form）。")
    print(f"等待回调（最多 {seconds:.0f} 秒）…")

    try:
        await asyncio.wait_for(handler.received.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        print("超时未收到表单回调：确认模板可用、卡片回调已开通")
        return 1

    await asyncio.sleep(2.5)
    print(f"卡片更新结果   : {handler.last_update or '未完成（看上方日志）'}")
    print("\n== 观察要点 ==")
    print("1. params 里是否有 form / current_form，字段值与填写是否一致")
    print("2. hidden 字段 token 是否原样回传（用于 forms 防重）")
    print("3. userId 是否为点击人明文 staffId")
    print("4. outTrackId 是否等于本轮发送的卡片实例 ID")
    return 0


class _VerifyEventHandler(EventHandler):
    """事件订阅 handler：让客户端上报 EVENT: *，供后台"验证连接通道"通过。"""

    async def process(self, event: EventMessage):
        topic = getattr(getattr(event, "headers", None), "topic", "")
        print(f"\n== 收到事件订阅推送 ==\ntopic={topic}")
        return AckMessage.STATUS_OK, "OK"


class _PrintHandler(ChatbotHandler):
    """打印真实回调帧，便于确认 senderStaffId / conversationType 等字段。"""

    def __init__(self) -> None:
        super().__init__()
        self.received = asyncio.Event()

    async def process(self, callback: CallbackMessage):
        data = callback.data if isinstance(callback.data, dict) else {}
        print("\n== 收到原始回调 ==")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])

        incoming = ChatbotMessage.from_dict(data)
        text = getattr(getattr(incoming, "text", None), "content", "") or ""
        print("\n== 关键字段 ==")
        print(f"msgId            : {incoming.message_id}")
        print(f"senderStaffId    : {incoming.sender_staff_id!r}  ← 会话记忆用的明文 userId")
        print(f"senderId         : {incoming.sender_id!r}")
        print(f"senderNick       : {incoming.sender_nick!r}")
        print(f"conversationType : {incoming.conversation_type!r}  （1=单聊 2=群聊）")
        print(f"conversationId   : {incoming.conversation_id!r}")
        print(f"robotCode        : {incoming.robot_code!r}")
        print(f"msgtype          : {incoming.message_type!r}")
        print(f"text             : {text!r}")
        print(f"sessionWebhook   : {dingtalk_api.mask_secret(getattr(incoming, 'session_webhook', ''))}")

        if text:
            # 用同一个 webhook 回一条，验证被动回复链路
            try:
                self.reply_text(f"收到：{text}", incoming)
                print("已通过 sessionWebhook 回复")
            except Exception as exc:  # noqa: BLE001
                print(f"sessionWebhook 回复失败：{exc}")

        self.received.set()
        return AckMessage.STATUS_OK, "OK"


async def _run_stream(seconds: float) -> int:
    client = DingTalkStreamClient(
        Credential(DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET),
        logger=logging.getLogger("dingtalk_stream"),
    )
    handler = _PrintHandler()
    client.register_callback_handler(ChatbotMessage.TOPIC, handler)
    # 注册 EVENT 订阅，使建连时 subscriptions 带上 EVENT: *，后台验证连接通道才能通过
    client.register_all_event_handler(_VerifyEventHandler())

    # SDK 的 start() 内部是无限重连循环，不提供优雅停止接口，
    # 这里只等首条消息，进程退出交给 main() 的 os._exit。
    asyncio.create_task(client.start())
    print("Stream 连接中（已订阅 CALLBACK 机器人消息 + EVENT 事件）")
    print(f"等待消息（最多 {seconds:.0f} 秒）… 请给机器人发一条文字；"
          f"也可此时到后台点『验证连接通道』并保存")
    try:
        await asyncio.wait_for(handler.received.wait(), timeout=seconds)
        print("\nStream 收消息验证通过")
        return 0
    except asyncio.TimeoutError:
        print("\n超时未收到消息：确认应用已发布、机器人已添加、消息接收模式为 Stream")
        return 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="钉钉渠道阶段 1 冒烟")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="打印 SDK 版本与配置（默认）")
    sub.add_parser("token", help="验证 accessToken 链路")

    push = sub.add_parser("push", help="主动推送单聊消息")
    push.add_argument("--user-id", required=True, help="接收人明文 userId")
    push.add_argument("--text", default=DEFAULT_PUSH_TEXT)

    group = sub.add_parser("group", help="主动推送群聊消息")
    group.add_argument("--conversation-id", required=True, help="群 openConversationId")
    group.add_argument("--text", default=DEFAULT_PUSH_TEXT)

    stream = sub.add_parser("stream", help="连接 Stream 并打印回调帧")
    stream.add_argument("--seconds", type=float, default=120.0)

    replay = sub.add_parser("replay", help="重放同一回调帧验证 msgId 去重")
    replay.add_argument("--file", help="回调 JSON 文件；缺省使用内置样例")

    card = sub.add_parser("card", help="发送 AI 卡片验证流式输出")
    card.add_argument("--user-id", required=True, help="接收人明文 userId")
    card.add_argument("--llm", action="store_true", help="用真实 LLM 生成《出师表》")
    card.add_argument("--interval", type=float, default=0.6, help="两次推流间隔（秒）")

    cardcb = sub.add_parser("cardcb", help="发送带按钮卡片并打印真实卡片回调帧")
    cardcb.add_argument("--user-id", required=True, help="接收人明文 userId")
    cardcb.add_argument("--seconds", type=float, default=180.0, help="等待点击的秒数")

    for name in ("aicard", "aicard-form"):
        aicard = sub.add_parser(name, help="发送 A2UI 卡片并打印真实回调帧")
        aicard.add_argument("--user-id", required=True, help="接收人明文 userId")
        aicard.add_argument("--seconds", type=float, default=180.0, help="等待操作的秒数")
        aicard.add_argument(
            "--space-type",
            default="im_robot",
            help="单聊固定 im_robot；若报 openSpaceId 非法可试 IM_ROBOT",
        )

    tplform = sub.add_parser("tplform", help="用官方公开表单模板发卡并打印回调帧")
    tplform.add_argument("--user-id", required=True, help="接收人明文 userId")
    tplform.add_argument("--seconds", type=float, default=180.0, help="等待操作的秒数")

    sendcard = sub.add_parser("sendcard", help="验证 4.1 通道发卡链路（不等待回调）")
    sendcard.add_argument("--user-id", required=True, help="接收人明文 userId")
    sendcard.add_argument(
        "--kind", choices=("form", "buttons"), default="form", help="卡片类型"
    )

    cardflow = sub.add_parser("cardflow", help="启动真实通道验证 4.2 回调派发")
    cardflow.add_argument("--user-id", required=True, help="接收人明文 userId")
    cardflow.add_argument(
        "--kind", choices=("form", "buttons"), default="form", help="卡片类型"
    )
    cardflow.add_argument("--seconds", type=float, default=180.0, help="等待操作的秒数")

    openform = sub.add_parser("openform", help="4.3 冒烟：直接走 flow 发创建任务卡")
    openform.add_argument("--user-id", required=True, help="接收人明文 userId")

    createflow = sub.add_parser("createflow", help="4.4 端到端：真实走完创建任务流程")
    createflow.add_argument("--user-id", required=True, help="接收人明文 userId")
    createflow.add_argument("--seconds", type=float, default=300.0, help="等待流程完成的秒数")

    startflow = sub.add_parser("startflow", help="4.5 端到端：走完启动任务流程")
    startflow.add_argument("--user-id", required=True, help="接收人明文 userId")
    startflow.add_argument("--seconds", type=float, default=300.0, help="等待流程完成的秒数")
    startflow.add_argument(
        "--real",
        action="store_true",
        help="真正调用启动接口；默认 dry-run，不会启动真实外呼",
    )

    statusflow = sub.add_parser("statusflow", help="4.6 端到端：筛选 → 列表 → 概况")
    statusflow.add_argument("--user-id", required=True, help="接收人明文 userId")
    statusflow.add_argument("--seconds", type=float, default=240.0, help="等待操作的秒数")
    statusflow.add_argument(
        "--keyword", default="", help="可选：按任务名/ID 直接查询"
    )

    args = parser.parse_args()

    if args.command in (None, "check"):
        return cmd_check()
    if args.command == "token":
        return cmd_token()
    if args.command == "push":
        return cmd_push(args.user_id, args.text)
    if args.command == "group":
        return cmd_group(args.conversation_id, args.text)
    if args.command == "card":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        return asyncio.run(
            _run_card(args.user_id, args.llm, max(0.1, args.interval)),
            loop_factory=asyncio.SelectorEventLoop,
        )
    if args.command == "cardcb":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(_run_card_probe(args.user_id, args.seconds))
        # SDK 的 start() 是无限重连循环，直接结束进程保证脚本可退出
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command in ("aicard", "aicard-form"):
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_a2ui_probe(
                args.user_id,
                args.space_type,
                args.seconds,
                args.command == "aicard-form",
            )
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "tplform":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_template_form_probe(args.user_id, args.seconds)
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "sendcard":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        return asyncio.run(
            _run_sendcard(args.user_id, args.kind),
            loop_factory=asyncio.SelectorEventLoop,
        )
    if args.command == "statusflow":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_status_flow(args.user_id, args.seconds, args.keyword)
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "startflow":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_start_flow(args.user_id, args.seconds, not args.real)
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "createflow":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_create_flow(args.user_id, args.seconds)
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "openform":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        return asyncio.run(
            _run_open_form(args.user_id),
            loop_factory=asyncio.SelectorEventLoop,
        )
    if args.command == "cardflow":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(
            _run_card_flow(args.user_id, args.kind, args.seconds)
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    if args.command == "replay":
        data = SAMPLE_CALLBACK
        if args.file:
            loaded = json.loads(Path(args.file).read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                print("回调文件内容不是 JSON 对象")
                return 1
            data = loaded
        return asyncio.run(
            _run_replay(data), loop_factory=asyncio.SelectorEventLoop
        )
    if args.command == "stream":
        if not dingtalk_api.is_configured():
            print("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先配置 .env")
            return 1
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
        code = loop.run_until_complete(_run_stream(args.seconds))
        # SDK 的 start() 会吞掉取消信号无限重连，直接结束进程保证脚本可退出
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
