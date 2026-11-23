"""企业微信智能机器人通道启动入口（独立脚本）。

运行：
    python run_wecom.py

长连接模式无需公网地址，只依赖 WECHAT_BOT_ID / WECHAT_BOT_SECRET。
"""

from __future__ import annotations

import asyncio
import logging

from app.clients import wecom_api
from app.core.config import Settings, get_env
from app.router import run_channel, setup_logging

log = logging.getLogger("run_wecom")


def main() -> None:
    setup_logging()

    if not wecom_api.is_configured():
        raise SystemExit("缺少 WECHAT_BOT_ID / WECHAT_BOT_SECRET，请先在 .env 中配置")

    # 提前校验模型与记忆库配置，避免长连接建立后才发现缺配置
    try:
        Settings.load()
    except RuntimeError as exc:
        raise SystemExit(f"[配置错误] {exc}") from exc
    if not get_env("PG_PASSWORD"):
        raise SystemExit("缺少 PG_PASSWORD，请在 .env 中配置 PostgreSQL 连接信息")

    log.info("启动通道：wecom")
    try:
        # psycopg 异步连接池不支持 Windows 默认的 ProactorEventLoop
        asyncio.run(run_channel("wecom"), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
