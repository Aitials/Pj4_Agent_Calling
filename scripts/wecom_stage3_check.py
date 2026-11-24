"""企业微信 · 阶段 3 冒烟测试（身份解析与记忆隔离）。

三种模式：
    # 1) 只验证自建应用凭证能否换到 access_token
    python scripts/wecom_stage3_check.py --token-only

    # 2) 验证单个 open_userid 能否转成明文 userid
    python scripts/wecom_stage3_check.py --userid wot7zxxxxxxxx

    # 3) 给机器人发一条消息，打印原始 ID 与解析后的会话标识
    python scripts/wecom_stage3_check.py --listen

注意：同一个 BotID 只允许一条长连接，--listen 前请先停掉 run_wecom.py。

记忆隔离的验证（手工执行）：
    select thread_id, count(*) from memory.checkpoints group by 1 order by 2 desc;
thread_id 是明文工号即转换生效；同一人两次问话应落在同一行。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.channels.wecom import WecomChannel  # noqa: E402
from app.clients import wecom_api  # noqa: E402

log = logging.getLogger("wecom_stage3")


def _token_check() -> int:
    if not wecom_api.is_identity_configured():
        print(
            "未配置 WECHAT_CORP_ID / WECHAT_AGENT_SECRET，无法换取 access_token",
            file=sys.stderr,
        )
        return 2

    token = wecom_api.access_token()
    if not token:
        print("FAIL 未能获取 access_token，请检查 CorpID / 应用 Secret", file=sys.stderr)
        return 1
    print(f"PASS access_token 已获取：{wecom_api.mask_secret(token)}")
    return 0


def _userid_check(raw: str) -> int:
    if not wecom_api.is_identity_configured():
        print(
            "未配置自建应用凭证：转换会直接降级为原始 ID，属预期行为",
            file=sys.stderr,
        )

    mapping = wecom_api.openuserid_to_userid([raw])
    if mapping is None:
        print(
            "FAIL 转换请求失败（网络或权限问题），实际运行时会降级为原始 ID",
            file=sys.stderr,
        )
    elif not mapping:
        print(f"PASS 平台未返回映射：{raw} 视为已明文，无需转换")
    else:
        print(f"PASS 转换成功 {raw} -> {mapping.get(raw, '-')}")

    resolved = wecom_api.resolve_userid(raw)
    print(f"resolve_userid({raw}) = {resolved}")
    return 0 if mapping is not None else 1


async def _listen(timeout: float) -> int:
    client = wecom_api.build_ws_client()
    channel = WecomChannel()
    channel._client = client

    got = asyncio.Event()
    state = {"raw": "", "plain": ""}

    client.on("authenticated", lambda: log.info("长连接认证成功"))
    client.on("disconnected", lambda reason: log.warning("长连接断开：%s", reason))
    client.on("error", lambda exc: log.error("长连接异常：%s", exc))

    async def on_message(frame: dict) -> None:
        if got.is_set():
            return
        body = frame.get("body") if isinstance(frame.get("body"), dict) else {}
        sender = body.get("from") if isinstance(body.get("from"), dict) else {}
        raw = str(sender.get("userid") or sender.get("user_id") or "")
        if not raw:
            return
        plain = await channel._resolve_user(raw)
        state["raw"], state["plain"] = raw, plain
        log.info("原始 ID=%s 解析后=%s", raw, plain)
        got.set()

    client.on("message", on_message)

    try:
        await client.connect()
    except Exception:  # noqa: BLE001
        log.exception("建立长连接失败")
        return 1

    log.info("请在企微里给机器人发送任意一条消息…")
    try:
        await asyncio.wait_for(got.wait(), timeout)
    except asyncio.TimeoutError:
        log.error("等待消息超时（%.0f 秒）", timeout)
        client.disconnect()
        return 1

    client.disconnect()
    raw, plain = state["raw"], state["plain"]
    if not plain:
        log.error("FAIL 未解析到身份")
        return 1
    if plain != raw:
        log.info("PASS 身份已转明文 userid=%s（thread_id 将使用该值）", plain)
    else:
        log.info("PASS 当前按原始 ID 使用（未配置凭证或无需转换）：%s", raw)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="企业微信阶段 3 冒烟测试")
    parser.add_argument("--token-only", action="store_true", help="只验证 access_token 获取")
    parser.add_argument("--userid", default="", help="待转换的 open_userid")
    parser.add_argument("--listen", action="store_true", help="监听入站消息并打印身份解析结果")
    parser.add_argument("--timeout", type=float, default=120, help="等待入站消息的秒数，默认 120")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.token_only:
        raise SystemExit(_token_check())
    if args.userid.strip():
        raise SystemExit(_userid_check(args.userid.strip()))
    if args.listen:
        if not wecom_api.is_configured():
            print("缺少 WECHAT_BOT_ID / WECHAT_BOT_SECRET，请先在 .env 中配置", file=sys.stderr)
            raise SystemExit(2)
        code = asyncio.run(
            _listen(args.timeout), loop_factory=asyncio.SelectorEventLoop
        )
        raise SystemExit(code)

    parser.print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
