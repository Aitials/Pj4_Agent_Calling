"""一知回调服务启动入口（独立进程）。

运行：
    python scripts/run_callback.py

服务默认只监听 127.0.0.1:8088，公网入口由端口映射工具转发。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import uvicorn  # noqa: E402

from app.core.config import get_env  # noqa: E402

log = logging.getLogger("aicc_callback")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    token = get_env("AICC_CALLBACK_TOKEN").strip()
    aes_key = get_env("AICC_CALLBACK_AES_KEY").strip()
    if not token or not aes_key:
        raise SystemExit(
            "缺少回调配置：请在 .env 中填写 AICC_CALLBACK_TOKEN 与 AICC_CALLBACK_AES_KEY"
        )
    if len(aes_key) != 43:
        raise SystemExit("AICC_CALLBACK_AES_KEY 必须是 43 位字符串")

    host = get_env("AICC_CALLBACK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(get_env("AICC_CALLBACK_PORT", "8088").strip() or 8088)
    path = get_env("AICC_CALLBACK_PATH", "/aicc/callback").strip() or "/aicc/callback"

    log.info("一知回调服务启动：http://%s:%s%s", host, port, path)
    uvicorn.run("app.callback.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
