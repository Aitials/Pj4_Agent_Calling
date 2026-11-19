"""客户分析子 Agent：读取本地业务数据，生成结构化跟进话术并写回。

与聊天主 Agent 完全独立：不接收完整 ASR 文本，由子 Agent 自行查库；
分析结果通过 save_customer_analysis 工具写入 agent_analysis。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.repositories import analysis_repo, call_record_repo, customer_repo

log = logging.getLogger("customer_agent")

INTENT_LABELS = {"HIGH": "高意向", "MEDIUM": "中意向", "LOW": "低意向", "INVALID": "无效"}

SYSTEM_PROMPT = (
    "你是客户外呼分析助手。你的任务是分析一通已结束的 AI 外呼通话，"
    "提炼客户真实需求、关注点、异议与意向依据，并生成可直接使用的销售跟进话术。\n\n"
    "工作步骤：\n"
    "1. 调用 get_call_record_detail 读取本通通话的完整 ASR 转写与意向信息；\n"
    "2. 若企业信用代码存在，调用 get_company_profile 查看企业档案与联系方式；\n"
    "3. 调用 get_call_history 查看该企业历史通话，避免重复或矛盾结论；\n"
    "4. 基于通话原文逐条提炼，不要编造转写里没有出现的信息；\n"
    "5. 必须调用 save_customer_analysis 写入结果，然后只输出一行 JSON，"
    '例如 {"success": true, "analysis_id": 501, "status": "DONE"}。\n\n'
    "判断要求：\n"
    "- intent_level 只能取 HIGH / MEDIUM / LOW / INVALID；\n"
    "- quote 必须摘自客户原话，不要改写；\n"
    "- 客户没有明确表达的内容，写「未提及」，不要猜测；\n"
    "- 话术要具体到本通电话的上下文，避免空泛套话。"
    '【销售话术生成规范】'
    '你必须基于工具返回的「企业档案 + 本通通话转写 + 历史通话」生成话术，禁止使用通用模板套话。'
    '每一段话术都要能回答：“这句话为什么适合这家企业、这次通话？”'
    '生成顺序：'
    '1. 先用 get_call_record_detail 拿本通完整转写；'
    '2. 再用 get_company_profile 拿企业档案（行业、地区、法人、参保人数、备注、联系电话）；'
    '3. 再用 get_call_history 看历史意向，避免与之前结论矛盾；'
    '4. 最后调用 save_customer_analysis，把结果写入结构化字段。'
    '硬性要求：'
    '- quote（客户原话）必须逐字摘自转写，不得改写、不得拼接；'
    '- 企业信息只能引用 get_company_profile 返回的字段，未公示字段写「未提及」；'
    '- 客户未表达的需求、预算、异议，一律写「未提及」，不要推断；'
    '- opening 必须同时包含：①企业或行业特征 ②本通通话中客户说过的具体内容；'
    '- value_points 必须与客户的 concerns / customer_needs 一一对应，禁止罗列无关产品卖点；'
    '- objection_handling 只处理 objections 中真实出现的异议，数量一一对应；'
    '- next_action 必须包含明确时间与动作（例如「次日 10:00 前电话回访，确认企微添加」）；'
    '- best_follow_at 保留模型输出原文，不要转成时间戳。'
    '话术输出结构（写入 save_customer_analysis 的对应字段）：'
    '1. opening：一段可直接照读的开场（2-3 句）'
    '2. value_points：3-5 条，每条格式「针对<客户某个关注点>：<具体价值点，含本通对话依据>」'
    '3. objection_handling：与客户异议逐条对应，每条格式「客户说：<原话摘录> → 回应：<具体回应>」'
    '4. next_action：一条，格式「时间：<建议时间>；动作：<具体动作>；目标：<下一步结果>」'
)


class _SavePayload(BaseModel):
    intent_level: str = Field(description="HIGH / MEDIUM / LOW / INVALID")
    reason: str = Field(description="意向判定理由，一句话")
    quote: str = Field(description="支撑判断的客户原话引用")
    customer_needs: list[str] = Field(description="客户需求列表，1-5 条")
    concerns: list[str] = Field(description="客户关注点列表，1-5 条")
    objections: list[str] = Field(description="客户异议列表，没有则空数组")
    intent_evidence: list[str] = Field(description="意向判断依据列表，引用通话事实")
    opening: str = Field(description="销售开场话术，结合本通通话内容")
    value_points: list[str] = Field(description="产品价值切入点列表")
    objection_handling: list[str] = Field(description="异议处理话术列表")
    next_action: str = Field(description="下一步跟进建议，含时间与动作")
    best_follow_at: str = Field(default="", description="建议联系时间，保留原文")
    budget: str = Field(default="", description="预算信息，未提及写「未提及」")
    blocked: int = Field(default=0, description="是否应阻断后续触达：0 否 1 是")
    block_reason: str = Field(default="", description="阻断原因：空号/明确拒绝/勿扰等")
    supplier_level: str = Field(default="", description="一知意向标签快照")


def _call_detail_text(record: dict) -> str:
    transcript = str(record.get("asr_transcript") or "").strip() or "（无转写）"
    return (
        f"record_id: {record.get('record_id')}\n"
        f"企业: {record.get('customer_name') or '未知'}\n"
        f"号码: {record.get('customer_phone')}\n"
        f"通话时间: {record.get('call_time')}，时长 {record.get('duration')} 秒，"
        f"{record.get('chat_round') or 0} 轮\n"
        f"一知意向: {record.get('supplier_level') or '-'}"
        f"（tag {record.get('intent_level_tag_id')} / code {record.get('intent_level_code')}）\n"
        f"判定依据: {record.get('analysis_basis') or '-'}\n"
        f"客户关注点: {record.get('customer_concern') or '-'}\n"
        f"挂断方: {record.get('hangup_by') or '-'}\n"
        f"\n完整转写：\n{transcript}"
    )


def _profile_text(profile: dict) -> str:
    lines = [
        f"企业名称: {profile.get('company_name') or '-'}",
        f"信用代码: {profile.get('credit_code') or '-'}",
        f"法人: {profile.get('legal_person') or '未公示'}",
        f"行业: {profile.get('industry') or '-'}",
        f"地区: {profile.get('region') or '-'}",
        f"累计外呼: {profile.get('call_times') or 0} 次",
        f"历史最高意向: {INTENT_LABELS.get(profile.get('best_intent_level'), profile.get('best_intent_level') or '-')}",
        f"最近意向: {INTENT_LABELS.get(profile.get('last_intent_level'), profile.get('last_intent_level') or '-')}",
        f"备注: {profile.get('note') or '-'}",
        "关联号码:",
    ]
    contacts = profile.get("contacts") or []
    if not contacts:
        lines.append("  （无）")
    for index, contact in enumerate(contacts[:10], start=1):
        lines.append(
            f"  {index}. {contact.get('phone')} "
            f"{'主号码' if contact.get('is_primary') else ''} "
            f"{'黑名单' if contact.get('is_blacklist') else ''}".strip()
        )
    return "\n".join(lines)


def _history_text(rows: list[dict]) -> str:
    if not rows:
        return "该企业暂无其他历史通话。"
    lines = []
    for row in rows:
        lines.append(
            f"- {row.get('call_time')} | {row.get('call_status')} | "
            f"意向 {row.get('supplier_level') or '-'}"
            f"（tag {row.get('intent_level_tag_id')}/{row.get('intent_level_code')}） | "
            f"客户末句：{row.get('last_message') or '-'}"
        )
    return "\n".join(lines)


def build_customer_agent(record: dict):
    """按单通通话构造子 Agent，工具通过闭包绑定上下文，避免模型编造主键。"""
    record_id = str(record.get("record_id") or "")
    credit_code = str(record.get("credit_code") or "")
    lead_brief_id = int(record.get("lead_brief_id") or 0) or None
    customer_phone = str(record.get("customer_phone") or "")

    @tool
    def get_call_record_detail(record_id_arg: str = "") -> str:
        """读取本通通话详情与完整 ASR 转写（只需传 record_id，留空默认当前通话）。"""
        target = (record_id_arg or record_id).strip()
        row = call_record_repo.get_call_record(target)
        if not row:
            return f"未找到通话记录 {target}"
        return _call_detail_text(row)

    @tool
    def get_company_profile() -> str:
        """查询本通通话所属企业的工商档案与全部联系电话（无需参数）。"""
        if not credit_code:
            return "本通通话未关联企业信用代码，无企业档案可查。"
        profile = customer_repo.get_profile(credit_code)
        if not profile:
            return f"未找到信用代码为 {credit_code} 的企业档案。"
        return _profile_text(profile)

    @tool
    def get_call_history(limit: int = 5) -> str:
        """查询本企业最近的历史通话，返回时间、状态、意向与客户末句（不含本通）。"""
        if not credit_code:
            return "本通通话未关联企业信用代码，无历史通话可查。"
        rows = _fetch_history(credit_code, record_id, max(1, min(int(limit or 5), 10)))
        return _history_text(rows)

    @tool
    def save_customer_analysis(
        intent_level: str,
        reason: str,
        quote: str,
        customer_needs: list[str],
        concerns: list[str],
        objections: list[str],
        intent_evidence: list[str],
        opening: str,
        value_points: list[str],
        objection_handling: list[str],
        next_action: str,
        best_follow_at: str = "",
        budget: str = "",
        blocked: int = 0,
        block_reason: str = "",
    ) -> str:
        """保存本通通话的结构化分析结果，返回 analysis_id。分析完成后必须调用一次。"""
        payload = _SavePayload(
            intent_level=intent_level,
            reason=reason,
            quote=quote,
            customer_needs=customer_needs,
            concerns=concerns,
            objections=objections,
            intent_evidence=intent_evidence,
            opening=opening,
            value_points=value_points,
            objection_handling=objection_handling,
            next_action=next_action,
            best_follow_at=best_follow_at,
            budget=budget,
            blocked=blocked,
            block_reason=block_reason,
            supplier_level=str(record.get("supplier_level") or ""),
        )
        analysis_id = analysis_repo.save_analysis(
            lead_brief_id=lead_brief_id,
            record_id=record_id,
            credit_code=credit_code or "UNKNOWN",
            customer_phone=customer_phone,
            intent_level=payload.intent_level.upper(),
            reason=payload.reason,
            quote=payload.quote,
            demand="；".join(payload.customer_needs),
            budget=payload.budget,
            objection="；".join(payload.objections),
            blocked=payload.blocked,
            block_reason=payload.block_reason,
            suggestion=payload.next_action,
            best_follow_at=payload.best_follow_at,
            supplier_level=payload.supplier_level,
            customer_needs=payload.customer_needs,
            concerns=payload.concerns,
            objections=payload.objections,
            intent_evidence=payload.intent_evidence,
            opening=payload.opening,
            value_points=payload.value_points,
            objection_handling=payload.objection_handling,
            next_action=payload.next_action,
            model=_settings().llm_model,
            raw_json=payload.model_dump(),
        )
        return json.dumps(
            {"success": True, "analysis_id": analysis_id, "status": "DONE"},
            ensure_ascii=False,
        )

    settings = _settings()
    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    agent = create_agent(
        llm,
        tools=[
            get_call_record_detail,
            get_company_profile,
            get_call_history,
            save_customer_analysis,
        ],
        system_prompt=SYSTEM_PROMPT,
    )

    user_prompt = (
        f"请分析这条通话并写入分析结果：\n"
        f"record_id={record_id}\n"
        f"credit_code={credit_code or '（无）'}\n"
        f"customer_phone={customer_phone}\n"
        f"lead_brief_id={lead_brief_id}"
    )
    return agent, user_prompt


def _fetch_history(credit_code: str, exclude_record_id: str, limit: int) -> list[dict]:
    conn = customer_repo.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT `record_id`, `call_time`, `call_status`, `supplier_level`, "
                "       `intent_level_tag_id`, `intent_level_code`, `last_message` "
                "FROM `call_record` "
                "WHERE `credit_code` = %s AND `record_id` <> %s "
                "ORDER BY `call_time` DESC LIMIT %s",
                (credit_code, exclude_record_id, limit),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _settings() -> Settings:
    return Settings.load()


def run_customer_agent(record: dict) -> dict[str, Any]:
    """执行子 Agent，返回最终消息文本与解析出的 JSON（若有）。"""
    agent, prompt = build_customer_agent(record)
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    messages = result.get("messages") if isinstance(result, dict) else []
    text = ""
    if messages:
        content = getattr(messages[-1], "content", "")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"text": text, "parsed": _extract_json(text)}


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except ValueError:
            return None
    return None
