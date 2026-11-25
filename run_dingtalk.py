"""钉钉渠道启动入口（独立脚本）。

运行：
    python run_dingtalk.py

机器人走 Stream 模式，无需公网回调地址；但主动发消息走 OpenAPI，
仍需在开发者后台配置服务端出口 IP 白名单。
"""

from __future__ import annotations

import asyncio
import logging

from app.clients import dingtalk_api
from app.core.config import Settings, get_env
from app.router import run_channel, setup_logging

log = logging.getLogger("run_dingtalk")


def main() -> None:
    setup_logging()

    if not dingtalk_api.is_configured():
        raise SystemExit("缺少 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET，请先在 .env 中配置")

    if not get_env("DINGTALK_ROBOT_CODE"):
        log.warning(
            "未配置 DINGTALK_ROBOT_CODE，主动推送与降级回复将不可用"
            "（sessionWebhook 被动回复不受影响）"
        )

    # 提前校验模型与记忆库配置，避免长连接建立后才发现缺配置
    try:
        Settings.load()
    except RuntimeError as exc:
        raise SystemExit(f"[配置错误] {exc}") from exc
    if not get_env("PG_PASSWORD"):
        raise SystemExit("缺少 PG_PASSWORD，请在 .env 中配置 PostgreSQL 连接信息")

    log.info("启动通道：dingtalk")
    try:
        # psycopg 异步连接池不支持 Windows 默认的 ProactorEventLoop
        asyncio.run(run_channel("dingtalk"), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
