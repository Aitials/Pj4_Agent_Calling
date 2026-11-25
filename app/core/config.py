"""应用配置：统一从 .env 读取，换模型只改环境变量，不改代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4.5-air"


def get_env(name: str, default: str = "") -> str:
    """读取环境变量（本模块导入时已加载 .env）。"""
    return os.getenv(name, default)


def _path_from_env(name: str, default: Path) -> Path:
    value = get_env(name).strip()
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


# ===== 聊天通道（飞书）=====
STATE_DIR = _path_from_env("STATE_DIR", PROJECT_ROOT / "state")
CHANNEL = get_env("CHANNEL", "feishu").strip().lower()
FEISHU_APP_ID = get_env("FEISHU_APP_ID").strip()
FEISHU_APP_SECRET = get_env("FEISHU_APP_SECRET").strip()
FEISHU_MEDIA_DIR = _path_from_env("FEISHU_MEDIA_DIR", STATE_DIR / "feishu_media")

# 群聊默认关闭：只处理单聊（p2p）消息，记忆 thread_id 收敛到 open_id
ALLOW_GROUP_CHAT = get_env("ALLOW_GROUP_CHAT", "false").strip().lower() in (
    "1", "true", "yes", "on",
)

# ===== 企业微信智能机器人（第二渠道，长连接）=====
# 企业微信管理后台 → 智能机器人 → API 模式 → 长连接，页面生成 Bot ID 与 Secret
WECHAT_BOT_ID = get_env("WECHAT_BOT_ID").strip()
WECHAT_BOT_SECRET = get_env("WECHAT_BOT_SECRET").strip()
# 自建应用凭证：换取 access_token（open_userid 转明文 userid；通讯录/待办后续也用它）
WECHAT_CORP_ID = get_env("WECHAT_CORP_ID").strip()
WECHAT_AGENT_ID = get_env("WECHAT_AGENT_ID").strip()
WECHAT_AGENT_SECRET = get_env("WECHAT_AGENT_SECRET").strip()

# ===== 钉钉企业内部应用（第三渠道，机器人 Stream 模式）=====
# 开发者后台 → 基础信息 → 凭证与基础信息：Client ID（旧称 AppKey）/ Client Secret
DINGTALK_CLIENT_ID = get_env("DINGTALK_CLIENT_ID").strip()
DINGTALK_CLIENT_SECRET = get_env("DINGTALK_CLIENT_SECRET").strip()
# 组织 corpId：本期无 H5 免登可留空
DINGTALK_CORP_ID = get_env("DINGTALK_CORP_ID").strip()
# 应用能力 → 机器人 → 机器人 ID：主动发单聊/群聊消息时的 robotCode
DINGTALK_ROBOT_CODE = get_env("DINGTALK_ROBOT_CODE").strip()
# 应用标识，预留（Stream 收发不依赖）
DINGTALK_AGENT_ID = get_env("DINGTALK_AGENT_ID").strip()
# 卡片平台 → 表单模板 ID；留空用官方公开测试模板，生产必须换成自有模板
DINGTALK_CARD_TEMPLATE_FORM = (
    get_env("DINGTALK_CARD_TEMPLATE_FORM").strip()
    or "280f6d7a-63bc-4905-bf3f-4c6d95e5166b.schema"
)

# ===== 一知回调安全 =====
# 时间窗校验，超过窗口的 timestamp 直接拒绝（秒）；0 表示关闭窗口校验
AICC_CALLBACK_MAX_SKEW_SECONDS = int(
    get_env("AICC_CALLBACK_MAX_SKEW_SECONDS", "300").strip() or 300
)
# 解密后明文尾部的 receiveid；未配置时跳过接收方标识校验
AICC_CALLBACK_RECEIVE_ID = get_env("AICC_CALLBACK_RECEIVE_ID").strip()


@dataclass(frozen=True)
class Settings:
    """LLM 连接配置。"""

    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_temperature: float = 0.3

    @classmethod
    def load(cls) -> "Settings":
        api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("未配置 LLM_API_KEY，请在项目根目录的 .env 中填写")

        return cls(
            llm_api_key=api_key,
            # 兼容 OpenAI 协议的服务都可直接替换 base_url
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            llm_model=os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL,
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "").strip() or 0.3),
        )
