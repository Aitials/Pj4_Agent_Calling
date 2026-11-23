"""企业微信智能机器人客户端工厂。

长连接模式只需 Bot ID + Secret，不涉及公网回调地址与消息加解密。
"""

from __future__ import annotations

import logging

from aibot import WSClient, WSClientOptions

from app.core.config import WECHAT_BOT_ID, WECHAT_BOT_SECRET

log = logging.getLogger("wecom_api")


class _SdkLogger:
    """把 SDK 日志接到项目统一的 logging 上。"""

    def debug(self, message: str, *args: object) -> None:
        log.debug(message, *args)

    def info(self, message: str, *args: object) -> None:
        log.info(message, *args)

    def warn(self, message: str, *args: object) -> None:
        log.warning(message, *args)

    def error(self, message: str, *args: object) -> None:
        log.error(message, *args)


def is_configured() -> bool:
    """是否已配置长连接所需凭证。"""
    return bool(WECHAT_BOT_ID and WECHAT_BOT_SECRET)


def build_ws_client() -> WSClient:
    """创建企微长连接客户端；缺凭证直接报错，避免静默失败。"""
    if not is_configured():
        raise RuntimeError("缺少 WECHAT_BOT_ID / WECHAT_BOT_SECRET，请在 .env 中配置")
    return WSClient(
        WSClientOptions(
            bot_id=WECHAT_BOT_ID,
            secret=WECHAT_BOT_SECRET,
            logger=_SdkLogger(),
            # 长连接常驻，断线无限重连
            max_reconnect_attempts=-1,
        )
    )
