"""一知回调签名与 AES 解密。

协议与微信/企微回调一致：
- 签名：sha1(sort([token, timestamp, nonce, encrypt]).join(""))
- AES：EncodingAESKey 43 位，补 "=" 后 base64 解码得到 32 字节密钥
- 明文：random(16B) + msg_len(4B 大端) + msg + receiveid
- 填充：PKCS#7，块大小 32

只做协议层，不涉及数据库与业务。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

PAD_BLOCK_SIZE = 32
RANDOM_SIZE = 16
LENGTH_SIZE = 4


class CallbackCryptoError(RuntimeError):
    """回调加解密配置或报文错误。"""


class SignatureError(CallbackCryptoError):
    """签名校验失败。"""


class DecryptError(CallbackCryptoError):
    """密文解密失败。"""


class CallbackCrypto:
    """按 Token + EncodingAESKey 完成验签与解密。"""

    def __init__(
        self,
        token: str,
        aes_key: str,
        max_skew_seconds: int = 300,
    ) -> None:
        token = (token or "").strip()
        aes_key = (aes_key or "").strip()
        if not token:
            raise CallbackCryptoError("AICC_CALLBACK_TOKEN 未配置")
        if len(aes_key) != 43:
            raise CallbackCryptoError("AICC_CALLBACK_AES_KEY 必须是 43 位字符串")
        try:
            key = base64.b64decode(aes_key + "=")
        except Exception as exc:  # noqa: BLE001
            raise CallbackCryptoError("AICC_CALLBACK_AES_KEY 不是合法 Base64") from exc
        if len(key) != 32:
            raise CallbackCryptoError("AICC_CALLBACK_AES_KEY 解码后必须是 32 字节")
        self.token = token
        self.key = key
        self.max_skew_seconds = max(0, int(max_skew_seconds or 0))

    # ------------------------------------------------------------------ 签名

    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        parts = sorted([self.token, str(timestamp), str(nonce), str(encrypt)])
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()

    def verify(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> None:
        expected = self.signature(timestamp, nonce, encrypt)
        actual = (msg_signature or "").strip().lower()
        if not actual or not hmac.compare_digest(expected, actual):
            raise SignatureError("msg_signature 校验失败")
        if self.max_skew_seconds:
            self._check_timestamp(timestamp)

    def _check_timestamp(self, timestamp: str) -> None:
        """校验时间戳窗口，配合 nonce 缓存防重放。"""
        try:
            value = int(str(timestamp).strip())
        except (TypeError, ValueError) as exc:
            raise SignatureError("timestamp 不是整数") from exc
        # 毫秒时间戳（> 1e12）先归一到秒
        if value > 10**12:
            value //= 1000
        if abs(time.time() - value) > self.max_skew_seconds:
            raise SignatureError("timestamp 超出允许的时间窗口")

    # ------------------------------------------------------------------ 解密

    def decrypt(self, encrypt_b64: str) -> str:
        """解密回调密文，返回消息明文（JSON 字符串）。"""
        return self.decrypt_with_receive_id(encrypt_b64)[0]

    def decrypt_with_receive_id(
        self, encrypt_b64: str, expected_receive_id: str = ""
    ) -> tuple[str, str]:
        """解密回调密文，返回 (消息明文, receive_id)。

        expected_receive_id 非空时校验明文尾部接收方标识，防止密文被换绑到其他主体。
        """
        try:
            raw = base64.b64decode(encrypt_b64, validate=True)
            cipher = AES.new(self.key, AES.MODE_CBC, self.key[:RANDOM_SIZE])
            plain = unpad(cipher.decrypt(raw), PAD_BLOCK_SIZE)
        except Exception as exc:  # noqa: BLE001
            raise DecryptError("AES 解密失败，请检查 EncodingAESKey 与密文") from exc

        if len(plain) < RANDOM_SIZE + LENGTH_SIZE:
            raise DecryptError("解密后明文长度不足")
        msg_len = int.from_bytes(plain[RANDOM_SIZE:RANDOM_SIZE + LENGTH_SIZE], "big")
        start = RANDOM_SIZE + LENGTH_SIZE
        end = start + msg_len
        if msg_len <= 0 or end > len(plain):
            raise DecryptError("解密后 msg_len 越界")
        try:
            message = plain[start:end].decode("utf-8")
            receive_id = plain[end:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DecryptError("解密后消息不是 UTF-8 文本") from exc

        expected = (expected_receive_id or "").strip()
        if expected and receive_id.strip() != expected:
            raise DecryptError("回调接收方标识不匹配")
        return message, receive_id

    def encrypt(self, message: str, receive_id: str = "") -> str:
        """按同一协议加密，仅用于自测与本地验证。"""
        msg = message.encode("utf-8")
        plain = (
            os.urandom(RANDOM_SIZE)
            + len(msg).to_bytes(LENGTH_SIZE, "big")
            + msg
            + (receive_id or "").encode("utf-8")
        )
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:RANDOM_SIZE])
        return base64.b64encode(cipher.encrypt(pad(plain, PAD_BLOCK_SIZE))).decode()
