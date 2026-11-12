"""终端入口：验证 LangChain + ChatOpenAI 调用链路是否打通。

用法：
    python main.py
    python main.py "你好，介绍下你自己"
"""

from __future__ import annotations

import sys

from app.agents.base_agent import BaseAgent
from app.core.config import Settings
from app.schemas.chat import AgentRequest, AgentResponse

DEFAULT_INPUT = "你好，请用一句话介绍你自己。"


def main() -> int:
    text = " ".join(sys.argv[1:]).strip() or DEFAULT_INPUT

    try:
        settings = Settings.load()
    except RuntimeError as exc:
        print(f"[配置错误] {exc}")
        return 1

    agent = BaseAgent(settings)
    request = AgentRequest(input=text)

    print(f"模型: {settings.llm_model}")
    print(f"输入: {request.input}")

    try:
        result = agent.invoke(request.input)
    except Exception as exc:  # 网络或鉴权异常
        print(f"[调用失败] {type(exc).__name__}: {exc}")
        return 1

    response = AgentResponse(output=result)
    print(f"输出: {response.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
