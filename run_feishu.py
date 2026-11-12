"""飞书通道启动入口（独立脚本）。

运行：
    python run_feishu.py
"""

from __future__ import annotations

import asyncio
import logging

# 必须在 asyncio.run 之前导入：lark_oapi.ws 在模块导入时就会抓取事件循环，
# 若导入发生在事件循环运行中，子线程里 run_until_complete 会抛
# "This event loop is already running"，导致长连接直接断开。
import lark_oapi.ws  # noqa: F401

from app.core.config import FEISHU_APP_ID, FEISHU_APP_SECRET
from app.router import get_agent, run_channel, setup_logging

log = logging.getLogger("run")


def main() -> None:
    setup_logging()

    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先在 .env 中配置")

    # 提前校验模型配置，避免长连接建立后才发现缺少 API Key
    try:
        get_agent()
    except RuntimeError as exc:
        raise SystemExit(f"[配置错误] {exc}") from exc

    log.info("启动通道：feishu")
    try:
        asyncio.run(run_channel("feishu"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
