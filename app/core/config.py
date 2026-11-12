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
