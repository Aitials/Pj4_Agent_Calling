"""对话历史数据访问层。

读写 MySQL 的 chat_history 表，连接信息取自 .env 的 MYSQL_*。
会话级短期记忆：每次调用 Agent 前读历史、调用后写回。
"""

from __future__ import annotations

import logging

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_env

log = logging.getLogger("chat_history")

TABLE = "chat_history"
CONVERSATION_DB = "agent_calling"


class ChatHistoryError(RuntimeError):
    """对话历史存储不可用。"""


# ------------------------------------------------------------------ 工具


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：总字符数 / 4。

    中文约 1.5 字符/token，英文约 4 字符/token，折中除以 4 够快速也够粗糙。
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# ------------------------------------------------------------------ 连接


def _connection_config() -> dict:
    password = get_env("MYSQL_PASSWORD")
    if not password:
        raise ChatHistoryError("缺少 MYSQL_PASSWORD，请在 .env 中配置 MySQL 连接信息")
    return {
        "host": get_env("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(get_env("MYSQL_PORT", "3306").strip() or 3306),
        "user": get_env("MYSQL_USER", "root").strip(),
        "password": password,
        "database": CONVERSATION_DB,
        "charset": "utf8mb4",
        "connect_timeout": 8,
        "cursorclass": DictCursor,
    }


def _connect():
    try:
        return pymysql.connect(**_connection_config())
    except pymysql.MySQLError as exc:
        raise ChatHistoryError(f"数据库连接失败：{exc}") from exc


# ------------------------------------------------------------------ 写


def save_message(conversation_id: str, open_id: str, role: str, content: str) -> int:
    """插入一条消息，返回自增 id。失败时抛 ChatHistoryError。"""
    conversation_id = (conversation_id or "").strip()
    open_id = (open_id or "").strip()
    role = (role or "").strip()
    content = content or ""

    if not conversation_id:
        raise ValueError("conversation_id 不能为空")
    if role not in ("user", "assistant"):
        raise ValueError(f"role 必须是 user 或 assistant，收到：{role}")

    token_len = estimate_tokens(content)

    sql = (
        f"INSERT INTO `{TABLE}` "
        "(`conversation_id`, `open_id`, `role`, `content`, `token_len`) "
        "VALUES (%s, %s, %s, %s, %s)"
    )

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (conversation_id, open_id, role, content, token_len))
            conn.commit()
            row_id = int(cur.lastrowid or 0)
            log.debug("已保存消息 #%d conversation=%s role=%s tokens=%d",
                      row_id, conversation_id, role, token_len)
            return row_id
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            raise ChatHistoryError(
                f"数据表 {CONVERSATION_DB}.{TABLE} 不存在，请先执行建表脚本"
            ) from exc
        raise ChatHistoryError(f"写入失败：{exc}") from exc
    except pymysql.MySQLError as exc:
        raise ChatHistoryError(f"写入失败：{exc}") from exc


def cleanup_old(conversation_id: str, keep: int = 200) -> int:
    """删除某会话最旧的、超出 keep 数量的消息，返回删除行数。

    每次写入 assistant 消息后调用一次，保证单会话不无限膨胀。
    """
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return 0

    sql = (
        f"DELETE FROM `{TABLE}` "
        "WHERE `conversation_id` = %s "
        "AND `id` NOT IN ( "
        "  SELECT `id` FROM ( "
        f"    SELECT `id` FROM `{TABLE}` "
        "    WHERE `conversation_id` = %s "
        "    ORDER BY `id` DESC "
        "    LIMIT %s "
        "  ) AS t "
        ")"
    )

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (conversation_id, conversation_id, keep))
            conn.commit()
            deleted = int(cur.rowcount or 0)
            if deleted:
                log.info("已清理 conversation=%s 的旧消息 %d 条", conversation_id, deleted)
            return deleted
    except pymysql.MySQLError as exc:
        log.warning("清理旧消息失败（不影响主流程）：%s", exc)
        return 0


# ------------------------------------------------------------------ 读


def load_history(
    conversation_id: str,
    max_tokens: int = 4000,
    max_messages: int = 100,
) -> list[dict]:
    """按 token 阈值裁剪，返回可供 LangChain 使用的 messages 列表。

    从数据库取最近 max_messages 条（倒序），然后从新往旧累加 token_len，
    超过 max_tokens 就停止，最后翻回正序返回。

    返回格式：[{"role": "user"|"assistant", "content": "..."}]
    """
    conversation_id = (conversation_id or "").strip()
    if not conversation_id:
        return []

    sql = (
        "SELECT `role`, `content`, `token_len` "
        f"FROM `{TABLE}` "
        "WHERE `conversation_id` = %s "
        "ORDER BY `created_at` DESC, `id` DESC "
        "LIMIT %s"
    )

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (conversation_id, max_messages))
            rows = cur.fetchall()
    except pymysql.err.ProgrammingError as exc:
        if exc.args and exc.args[0] == 1146:
            raise ChatHistoryError(
                f"数据表 {CONVERSATION_DB}.{TABLE} 不存在，请先执行建表脚本"
            ) from exc
        raise ChatHistoryError(f"读取失败：{exc}") from exc
    except pymysql.MySQLError as exc:
        raise ChatHistoryError(f"读取失败：{exc}") from exc

    if not rows:
        return []

    # 从新往旧累加，超阈值就停
    total = 0
    kept_reversed: list[dict] = []
    for row in rows:
        token_len = int(row.get("token_len") or estimate_tokens(row.get("content") or ""))
        if total + token_len > max_tokens:
            break
        kept_reversed.append({
            "role": str(row["role"]),
            "content": str(row["content"] or ""),
        })
        total += token_len

    # 翻回正序（最旧在前）
    kept_reversed.reverse()
    log.debug("加载历史 conversation=%s messages=%d tokens=%d (max=%d)",
              conversation_id, len(kept_reversed), total, max_tokens)
    return kept_reversed
