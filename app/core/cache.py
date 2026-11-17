"""Redis 缓存层：给外部平台（一知 AICC 等）的只读数据做短期缓存。

设计取舍：
- 同步实现，与现有 @tool 工具（同步函数）直接配合，不引入 async 复杂度；
- Redis 不可用时**自动降级为不缓存**并只告警一次，保证工具本身仍能返回数据；
- 只缓存可序列化的 JSON / 字符串，键统一加业务前缀，便于用 `KEYS aicc:*` 排查。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

import redis

from app.core.config import get_env

log = logging.getLogger("cache")

REDIS_HOST = get_env("REDIS_HOST", "127.0.0.1").strip() or "127.0.0.1"
REDIS_PORT = int(get_env("REDIS_PORT", "6379").strip() or 6379)
REDIS_DB = int(get_env("REDIS_DB", "0").strip() or 0)
REDIS_PASSWORD = get_env("REDIS_PASSWORD", "").strip() or None
REDIS_TIMEOUT = float(get_env("REDIS_TIMEOUT", "2").strip() or 2)

# 看板类数据默认缓存 10 分钟
DASHBOARD_TTL = int(get_env("AICC_DASHBOARD_TTL", "600").strip() or 600)
# 任务状态需要接近实时，默认 60 秒
TASK_STATUS_TTL = int(get_env("AICC_TASK_STATUS_TTL", "60").strip() or 60)

_client: redis.Redis | None = None
_redis_down = False


def get_client() -> redis.Redis:
    """惰性创建并复用连接池；连接失败由调用方按降级处理。"""
    global _client
    if _client is None:
        _client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            socket_connect_timeout=REDIS_TIMEOUT,
            socket_timeout=REDIS_TIMEOUT,
            decode_responses=True,
        )
    return _client


def ping() -> tuple[bool, str]:
    """供启动自检和工具兜底提示使用：(是否可用, 说明)。"""
    try:
        get_client().ping()
        return True, f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB} 正常"
    except redis.RedisError as exc:
        return False, f"Redis 不可用（{exc.__class__.__name__}）：{exc}"


def get(key: str) -> str | None:
    if _redis_down:
        return None
    try:
        return get_client().get(key)
    except redis.RedisError as exc:
        _warn_once(exc)
        return None


def delete(key: str) -> None:
    """主动失效单个键（用于「刷新数据」时跳过缓存）。"""
    if _redis_down:
        return
    try:
        get_client().delete(key)
    except redis.RedisError as exc:
        _warn_once(exc)


def set(key: str, value: str, ttl: int = DASHBOARD_TTL) -> None:
    if _redis_down:
        return
    try:
        get_client().set(key, value, ex=ttl)
    except redis.RedisError as exc:
        _warn_once(exc)


def get_json(key: str) -> Any | None:
    raw = get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("缓存内容不是合法 JSON，已忽略：%s", key)
        return None


def set_json(key: str, value: Any, ttl: int = DASHBOARD_TTL) -> None:
    set(key, json.dumps(value, ensure_ascii=False), ttl)


def get_or_load(key: str, loader: Callable[[], Any], ttl: int = DASHBOARD_TTL) -> tuple[Any, bool]:
    """先读缓存，未命中再执行 loader。

    返回 (数据, 是否命中缓存)。loader 抛异常时不写缓存，异常原样抛给调用方。
    """
    cached = get_json(key)
    if cached is not None:
        return cached, True

    value = loader()
    if value is not None:
        set_json(key, value, ttl)
    return value, False


def _warn_once(exc: Exception) -> None:
    global _redis_down
    if not _redis_down:
        _redis_down = True
        log.warning("Redis 不可用，缓存降级为直连：%s", exc)
