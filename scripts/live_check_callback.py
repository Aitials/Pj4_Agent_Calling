"""从公网侧验证回调服务（走隧道，真实验签 + 解密 + 入库）。

用法:
    python scripts/live_check_callback.py                                   # 只打本地
    python scripts/live_check_callback.py --base https://frp-ski.com:19704  # 打隧道
    python scripts/live_check_callback.py --base https://xxx --post         # 再推一条真实回调入库
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

from app.callback.crypto import CallbackCrypto  # noqa: E402
from app.core.config import get_env  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="", help="如 https://frp-ski.com:19704，缺省用 .env 的本地地址")
    ap.add_argument("--post", action="store_true", help="额外推一条业务回调（会写库，record_id 带 SELFTEST-）")
    args = ap.parse_args()

    path = get_env("AICC_CALLBACK_PATH", "/aicc/callback") or "/aicc/callback"
    if args.base:
        base = args.base.rstrip("/")
    else:
        host = get_env("AICC_CALLBACK_HOST", "127.0.0.1") or "127.0.0.1"
        port = get_env("AICC_CALLBACK_PORT", "8088") or "8088"
        base = f"http://{host}:{port}"

    crypto = CallbackCrypto(get_env("AICC_CALLBACK_TOKEN"), get_env("AICC_CALLBACK_AES_KEY"))
    url = f"{base}{path}"
    print(f"目标: {url}\n")

    ok = True
    # trust_env=False：绕过系统 HTTP_PROXY，直连隧道
    with httpx.Client(timeout=15, trust_env=False, verify=False) as cli:
        try:
            h = cli.get(f"{base}/healthz")
            good = h.status_code == 200 and h.json().get("ok") is True
            print(f"  {'✓' if good else '✗'} healthz  {h.status_code}  {h.text.strip()[:80]}")
            ok &= good
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ healthz 异常 {type(exc).__name__}: {exc}")
            return 1

        plain = json.dumps({"ping": "live"}, ensure_ascii=False)
        encrypt = crypto.encrypt(plain)
        ts, nonce = "1731900111", "livecheck"
        sig = crypto.signature(ts, nonce, encrypt)

        r = cli.get(url, params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": encrypt})
        good = r.status_code == 200 and r.text == plain
        print(f"  {'✓' if good else '✗'} GET 验签解密  {r.status_code}  {'明文一致' if good else repr(r.text[:60])}")
        ok &= good

        bad = cli.get(url, params={"msg_signature": "0" * 40, "timestamp": ts, "nonce": nonce, "echostr": encrypt})
        good = bad.status_code == 403
        print(f"  {'✓' if good else '✗'} GET 错签名  {bad.status_code}  (期望 403)")
        ok &= good

        if args.post:
            from scripts.selftest_callback import build_payload  # 复用同一份报文

            rid = f"SELFTEST-TUNNEL-{datetime.now().strftime('%H%M%S')}"
            payload = json.dumps(build_payload(rid), ensure_ascii=False)
            enc = crypto.encrypt(payload)
            s2 = crypto.signature(ts, nonce, enc)
            body = f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt></xml>".encode()
            p = cli.post(url, params={"msg_signature": s2, "timestamp": ts, "nonce": nonce}, content=body)
            good = p.status_code == 200 and p.text.strip() == "success"
            print(f"  {'✓' if good else '✗'} POST 回调入库  {p.status_code}  {repr(p.text[:40])}  record_id={rid}")
            ok &= good

    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
