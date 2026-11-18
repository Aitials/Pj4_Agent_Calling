"""回调服务本地自检（不需要公网、不需要端口映射、不需要真打电话）。

模拟一知服务器的两种请求，验证「验签 → 解密 → 归一化 → 入库」整条链路：

    GET  /aicc/callback?msg_signature&timestamp&nonce&echostr   保存配置时的 URL 验证
    POST /aicc/callback?msg_signature&timestamp&nonce  (XML 密文)  业务回调

用法:
    python scripts/selftest_callback.py            # 不写库，只校验解析结果（安全）
    python scripts/selftest_callback.py --db       # 真实写入 MySQL（record_id 带 SELFTEST- 前缀）

退出码 0 = 全部通过。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.callback import service as callback_service  # noqa: E402
from app.callback.crypto import CallbackCrypto  # noqa: E402
from app.callback.server import CALLBACK_PATH, app  # noqa: E402
from app.core.config import get_env  # noqa: E402
from app.repositories import call_record_repo  # noqa: E402

OK = "\033[32m✓\033[0m"
NG = "\033[31m✗\033[0m"
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {OK if cond else NG} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def build_payload(record_id: str) -> dict:
    """按官方文档「通话记录回调接口」的报文结构构造一条测试数据。"""
    return {
        "dataType": "ROBOT_CALL_RECORD",
        "data": {
            "callRecordId": record_id,
            "tenantId": 8369,
            "robotCallJobId": 1704345203463,
            "robotCallRobotName": "test111",
            "dialogFlowId": 66286,
            "calledPhoneNumber": "13683015927",
            "customerPersonName": "自检客户",
            "resultStatus": "ANSWERED",
            "customerConcern": ["价格", "售后"],
            "fullAudioUrl": "https://example.com/ai_user.wav",
            "analysisBasis": "命中节点：明确肯定, 该节点设置了意向等级为：A(同意加微信)",
            "startTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "chatDuration": 68,
            "chatRound": 6,
            "ringDuration": 12,
            "hangupBy": "CUSTOMER",
            "intentLevelTagId": 1823,
            "intentLevelCode": 0,
            "intentLevelDetailName": "A(同意加微信)",
            "properties": {"credit_code": "91330100MA2SELFTEST", "local_job_no": "T-SELFTEST-01"},
            "dynamicProperties": {"姓名": "自检", "性别": "女士"},
            # 故意带上 ASR 流式片段，验证 _build_transcript 是否原样保留
            "callDetailList": [
                {"type": "ROBOT", "text": "喂，您好", "callDetailId": 1},
                {"type": "PERSON", "text": "加", "startOffset": 28840, "endOffset": 29440, "callDetailId": 2},
                {
                    "type": "PERSON",
                    "text": "加微信不方便，你先介绍一下吧",
                    "startOffset": 28840,
                    "endOffset": 31640,
                    "callDetailId": 3,
                },
                {"type": "ROBOT", "text": "好的，那您留意下微信服务通知", "callDetailId": 4},
            ],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true", help="真实写入 MySQL（默认只解析不落库）")
    args = ap.parse_args()

    token = get_env("AICC_CALLBACK_TOKEN").strip()
    aes_key = get_env("AICC_CALLBACK_AES_KEY").strip()
    if not token or not aes_key:
        raise SystemExit("缺少 AICC_CALLBACK_TOKEN / AICC_CALLBACK_AES_KEY，请先配置 .env")

    crypto = CallbackCrypto(token, aes_key)
    client = TestClient(app)
    record_id = f"SELFTEST-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    payload = build_payload(record_id)
    plaintext = json.dumps(payload, ensure_ascii=False)
    encrypt = crypto.encrypt(plaintext)
    ts, nonce = "1731900000", "selftest"

    print(f"\n== 1. URL 验证（GET {CALLBACK_PATH}）==")
    sig = crypto.signature(ts, nonce, encrypt)
    r = client.get(
        CALLBACK_PATH,
        params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": encrypt},
    )
    check("HTTP 200", r.status_code == 200, f"status={r.status_code}")
    check("返回解密后的明文", r.text == plaintext, f"len={len(r.text)}")
    check("Content-Type 是 text/plain", "text/plain" in r.headers.get("content-type", ""))

    print("\n== 2. 业务回调（POST 密文 XML）==")
    captured: dict = {}

    def fake_upsert(row: dict) -> None:
        captured.update(row)

    original = call_record_repo.upsert_call_record
    if not args.db:
        callback_service.call_record_repo.upsert_call_record = fake_upsert
    try:
        body = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>"
        r = client.post(
            CALLBACK_PATH,
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
            content=body.encode(),
            headers={"Content-Type": "text/plain"},
        )
    finally:
        callback_service.call_record_repo.upsert_call_record = original

    check("HTTP 200", r.status_code == 200, f"status={r.status_code}")
    check("响应体为 success", r.text.strip() == "success", repr(r.text[:40]))

    row = captured
    if not args.db and row:
        check("record_id 正确", row.get("record_id") == record_id, str(row.get("record_id")))
        check("通话状态归一化", row.get("call_status") not in (None, "", "UNKNOWN"), str(row.get("call_status")))
        check("时长解析", row.get("duration") == 68, str(row.get("duration")))
        check("意向 tag/code", (row.get("intent_level_tag_id"), row.get("intent_level_code")) == (1823, 0))
        check("credit_code 取自 properties", row.get("credit_code") == "91330100MA2SELFTEST")
        check("录音过期日已计算", row.get("record_expire_at") is not None, str(row.get("record_expire_at")))
        transcript = row.get("asr_transcript") or ""
        check("转写已拼接", "加微信不方便" in transcript, f"{len(transcript.splitlines())} 行")
        check("首句为机器人开场", row.get("first_message") == "喂，您好", str(row.get("first_message")))
        check("原始报文已保留", isinstance(row.get("raw_json"), dict))

    print("\n== 3. 幂等（同一 record_id 再推一次）==")
    calls: list[str] = []
    callback_service.call_record_repo.upsert_call_record = lambda r: calls.append(r.get("record_id"))
    try:
        r2 = client.post(
            CALLBACK_PATH,
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
            content=f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>".encode(),
        )
    finally:
        callback_service.call_record_repo.upsert_call_record = original
    check("重复回调仍返回 success", r2.status_code == 200 and r2.text.strip() == "success")
    check("重复回调走 upsert（非 insert）", calls == [record_id], str(calls))

    print("\n== 4. 异常分支 ==")
    r3 = client.post(
        CALLBACK_PATH,
        params={"msg_signature": "0" * 40, "timestamp": ts, "nonce": nonce},
        content=body.encode(),
    )
    check("签名错误返回 403", r3.status_code == 403, f"status={r3.status_code}")
    r4 = client.get(
        CALLBACK_PATH,
        params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
    )
    check("缺少 echostr 返回 400", r4.status_code == 400, f"status={r4.status_code}")
    r5 = client.post(
        CALLBACK_PATH,
        params={"msg_signature": sig, "timestamp": ts, "nonce": nonce},
        content=b"<xml></xml>",
    )
    check("缺少 Encrypt 返回 400", r5.status_code == 400, f"status={r5.status_code}")

    print("\n== 5. 配置回显（填入 AICC 后台用）==")
    print(f"  回调地址路径 : {CALLBACK_PATH}")
    print(f"  Token        : {token[:6]}…（完整值见 .env 的 AICC_CALLBACK_TOKEN）")
    print(f"  EncodingAESKey: {aes_key[:6]}…（43 位，见 .env 的 AICC_CALLBACK_AES_KEY）")
    if args.db:
        print(f"\n  已真实写入 MySQL，record_id = {record_id}")
        try:
            saved = call_record_repo.get_call_record(record_id)
            check("数据库可查到该记录", saved is not None, str((saved or {}).get("call_status")))
        except Exception as exc:  # noqa: BLE001
            check("数据库回查", False, f"{type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"{NG} 失败 {len(failures)} 项：" + "、".join(failures))
        return 1
    print(f"{OK} 全部通过（{8 + (3 if not args.db else 1)} 项断言）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
