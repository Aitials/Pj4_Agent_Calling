"""一知回调 HTTP 服务（FastAPI）。

路由：
    GET  /aicc/callback   验证 URL：验签 → 解密 echostr → 原样返回明文
    POST /aicc/callback   业务回调：验签 → 解密 → 归一化 → 入库 → 200 success

只做接收与入库，不触发 AI 分析，不发送飞书通知。
"""

from __future__ import annotations

import logging

from defusedxml import ElementTree as DefusedET
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.callback.crypto import (
    CallbackCrypto,
    CallbackCryptoError,
    DecryptError,
    SignatureError,
)
from app.callback.service import handle_plaintext
from app.core.config import get_env

log = logging.getLogger("aicc_callback")

MAX_BODY_BYTES = 2 * 1024 * 1024


def _callback_path() -> str:
    path = (get_env("AICC_CALLBACK_PATH", "/aicc/callback") or "/aicc/callback").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/aicc/callback"


CALLBACK_PATH = _callback_path()

app = FastAPI(
    title="AICC Callback Receiver",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

_crypto: CallbackCrypto | None = None


def _get_crypto() -> CallbackCrypto:
    global _crypto
    if _crypto is None:
        _crypto = CallbackCrypto(
            get_env("AICC_CALLBACK_TOKEN"),
            get_env("AICC_CALLBACK_AES_KEY"),
        )
    return _crypto


def _extract_encrypt(body: bytes) -> str:
    try:
        root = DefusedET.fromstring(body)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("回调报文不是合法 XML") from exc
    node = root.find("Encrypt")
    value = (node.text or "").strip() if node is not None else ""
    if not value:
        raise ValueError("回调报文缺少 Encrypt")
    return value


def _missing_params(params) -> list[str]:
    return [
        name
        for name in ("msg_signature", "timestamp", "nonce")
        if not (params.get(name) or "").strip()
    ]


def _error(message: str, status_code: int) -> PlainTextResponse:
    log.warning("回调拒绝：%s（HTTP %s）", message, status_code)
    return PlainTextResponse(message, status_code=status_code)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    configured = bool(get_env("AICC_CALLBACK_TOKEN").strip()) and bool(
        get_env("AICC_CALLBACK_AES_KEY").strip()
    )
    return JSONResponse({"ok": True, "configured": configured, "path": CALLBACK_PATH})


@app.get(CALLBACK_PATH)
@app.get(CALLBACK_PATH + "/")
async def verify_url(request: Request) -> PlainTextResponse:
    """一知保存回调地址时发起的 URL 有效性验证。"""
    params = request.query_params
    missing = _missing_params(params)
    echostr = (params.get("echostr") or "").strip()
    if missing or not echostr:
        return _error(f"缺少参数：{', '.join(missing + ([] if echostr else ['echostr']))}", 400)

    try:
        crypto = _get_crypto()
        crypto.verify(
            params.get("msg_signature") or "",
            params.get("timestamp") or "",
            params.get("nonce") or "",
            echostr,
        )
        plaintext = crypto.decrypt(echostr)
    except SignatureError as exc:
        return _error(str(exc), 403)
    except DecryptError as exc:
        return _error(str(exc), 400)
    except CallbackCryptoError as exc:
        # 配置缺失或密钥格式错误，返回 500 便于运维定位
        return _error(str(exc), 500)
    except Exception as exc:  # noqa: BLE001
        log.exception("GET 验证异常")
        return _error(f"验证失败：{exc}", 500)

    return PlainTextResponse(plaintext, media_type="text/plain")


@app.post(CALLBACK_PATH)
@app.post(CALLBACK_PATH + "/")
async def receive(request: Request) -> PlainTextResponse:
    """接收通话记录等业务回调。"""
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _error("回调报文超过 2MB", 413)

    params = request.query_params
    missing = _missing_params(params)
    if missing:
        return _error(f"缺少参数：{', '.join(missing)}", 400)

    try:
        encrypt = _extract_encrypt(body)
    except ValueError as exc:
        return _error(str(exc), 400)

    try:
        crypto = _get_crypto()
        crypto.verify(
            params.get("msg_signature") or "",
            params.get("timestamp") or "",
            params.get("nonce") or "",
            encrypt,
        )
    except SignatureError as exc:
        return _error(str(exc), 403)
    except CallbackCryptoError as exc:
        return _error(str(exc), 500)

    try:
        plaintext = crypto.decrypt(encrypt)
    except DecryptError as exc:
        return _error(str(exc), 400)

    try:
        handle_plaintext(plaintext)
    except ValueError as exc:
        # 报文结构错误，重试也不会成功
        return _error(f"回调内容解析失败：{exc}", 400)
    except Exception:  # noqa: BLE001
        # 数据库等暂时性故障，返回 500 让平台按 1/5/25 分钟重试
        log.exception("回调入库失败")
        return _error("入库失败", 500)

    # 平台以 HTTP 200 判定成功；正文返回 success 兼容旧版 FAQ 要求
    return PlainTextResponse("success", media_type="text/plain")
