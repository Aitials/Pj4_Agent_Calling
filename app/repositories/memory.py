"""Agent 短期记忆：PostgreSQL checkpoint 存储。

用 LangGraph 官方 AsyncPostgresSaver 持久化会话状态，thread_id 即会话标识
（飞书群聊 chat_id / 单聊 open_id）。不再有自定义 chat_history 表，也不再手工
拼接历史上下文。

checkpoint 表由 saver.setup() 自动创建：
    checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations
"""

from __future__ import annotations

import logging
import re

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_env

log = logging.getLogger("memory")

DEFAULT_SCHEMA = "memory"
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_pool: AsyncConnectionPool | None = None
_saver: AsyncPostgresSaver | None = None


class MemoryStoreError(RuntimeError):
    """短期记忆存储不可用。"""


# ------------------------------------------------------------------ 配置


def _schema() -> str:
    schema = get_env("PG_SCHEMA", DEFAULT_SCHEMA).strip() or DEFAULT_SCHEMA
    if not _SCHEMA_RE.match(schema):
        raise MemoryStoreError(f"PG_SCHEMA 非法：{schema}（只允许字母、数字、下划线）")
    return schema


def _conninfo(schema: str) -> str:
    password = get_env("PG_PASSWORD")
    if not password:
        raise MemoryStoreError(
            "缺少 PG_PASSWORD，请在 .env 中配置 PostgreSQL 连接信息"
        )
    return make_conninfo(
        host=get_env("PG_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=int(get_env("PG_PORT", "5432").strip() or 5432),
        user=get_env("PG_USER", "postgres").strip() or "postgres",
        password=password,
        dbname=get_env("PG_DATABASE", "agent_calling_memory").strip()
        or "agent_calling_memory",
        sslmode=get_env("PG_SSLMODE", "disable").strip() or "disable",
        # 让 saver.setup() 自动建表落到独立 schema
        options=f"-c search_path={schema},public",
    )


# ------------------------------------------------------------------ 生命周期


async def init_memory() -> AsyncPostgresSaver:
    """建连接池 + 建 schema + 建 checkpoint 表，返回全局唯一的 saver。"""
    global _pool, _saver
    if _saver is not None:
        return _saver

    schema = _schema()
    pool = AsyncConnectionPool(
        conninfo=_conninfo(schema),
        min_size=1,
        max_size=int(get_env("PG_POOL_MAX", "5").strip() or 5),
        open=False,
        timeout=15,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    )

    try:
        await pool.open()
        async with pool.connection() as conn:
            await conn.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(schema)
                )
            )
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
    except Exception:
        await pool.close()
        raise

    _pool, _saver = pool, saver
    log.info(
        "短期记忆已就绪：PG %s:%s/%s schema=%s",
        get_env("PG_HOST", "127.0.0.1"),
        get_env("PG_PORT", "5432"),
        get_env("PG_DATABASE", "agent_calling_memory"),
        schema,
    )
    return saver


async def close_memory() -> None:
    """进程退出时关闭连接池。"""
    global _pool, _saver
    if _pool is not None:
        await _pool.close()
        log.info("短期记忆连接池已关闭")
    _pool = None
    _saver = None


def get_saver() -> AsyncPostgresSaver | None:
    """已初始化的 saver，未初始化返回 None。"""
    return _saver
