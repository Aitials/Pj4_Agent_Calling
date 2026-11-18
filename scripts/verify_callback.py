"""回调本地自检：模拟一知发起 GET 验证与 POST 通话记录，并核对入库结果。

用法：
    python scripts/verify_callback.py
    python scripts/verify_callback.py --base-url https://xxx.example.com
    python scripts/verify_callback.py --repeat 2 --record-id 900001

默认读取 .env 中的 AICC_CALLBACK_TOKEN / AICC_CALLBACK_AES_KEY，与本机服务保持一致。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

from app.callback.crypto import CallbackCrypto  # noqa: E402
from app.core.config import get_env  # noqa: E402
from app.repositories.call_record_repo import (  # noqa: E402
    count_call_records,
    get_call_record,
)


def build_payload(record_id: str) -> dict:
    return {
        "dataType": "RECONFIGURATION_ROBOT_CALL_RECORD",
        "data": {
            "callOutRecordId": record_id,
            "callOutJobId": 999001,
            "callOutJobName": "本地回调自检任务",
            "dialogFlowId": 1001,
            "dialogFlowName": "自检话术",
            "accountName": "张明",
            "calledPhoneNumber": "13800000001",
            "startTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chatDuration": 42,
            "resultStatus": "ANSWERED",
            "intentLevelTagId": 910,
            "intentLevel": 1,
            "intentLevelName": "B(可能有意向)",
            "analysisBasis": "自检报文：命中意向节点",
            "customerConcern": ["价格", "上线时间"],
            "hangupBy": "REMOTE_HANGUP",
            "chatRound": 2,
            "ringDuration": 5,
            "fullAudioUrl": "https://example.com/callback-selftest.wav",
            "properties": {
                "credit_code": "91330106MA2TST001A",
                "local_job_no": "JOB20261116-01",
            },
            "dynamicVariables": {"姓名": "张明", "结果": "有意向"},
            "callDetailList": [
                {"text": "您好，我是智能外呼助手。", "type": 2, "index": 0},
                {"text": "你好，什么事？", "type": 1, "index": 1},
                {"text": "想确认一下您这边是否有采购计划。", "type": 2, "index": 2},
            ],
        },
    }


def check_get(client: httpx.Client, path: str, crypto: CallbackCrypto) -> bool:
    timestamp = str(int(time.time()))
    nonce = "1234567890"
    echo = crypto.encrypt("callback-ping-ok")
    signature = crypto.signature(timestamp, nonce, echo)
    response = client.get(
        path,
        params={
            "msg_signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echo,
        },
    )
    ok = response.status_code == 200 and response.text == "callback-ping-ok"
    print(
        f"[GET] {response.status_code} "
        f"{'PASS' if ok else 'FAIL'} body={response.text[:80]!r}"
    )
    return ok


def post_once(client: httpx.Client, path: str, crypto: CallbackCrypto, payload: dict) -> bool:
    plaintext = json.dumps(payload, ensure_ascii=False)
    encrypt = crypto.encrypt(plaintext)
    timestamp = str(int(time.time()))
    nonce = "0987654321"
    signature = crypto.signature(timestamp, nonce, encrypt)
    body = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>".encode("utf-8")
    response = client.post(
        path,
        params={
            "msg_signature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        },
        content=body,
        headers={"Content-Type": "text/xml"},
    )
    ok = response.status_code == 200 and response.text.strip() == "success"
    print(
        f"[POST] {response.status_code} "
        f"{'PASS' if ok else 'FAIL'} body={response.text[:80]!r}"
    )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="一知回调本地自检")
    parser.add_argument(
        "--base-url",
        default=f"http://127.0.0.1:{get_env('AICC_CALLBACK_PORT', '8088')}",
        help="服务地址，默认本机 8088；公网联调时填映射工具地址",
    )
    parser.add_argument(
        "--path",
        default=get_env("AICC_CALLBACK_PATH", "/aicc/callback") or "/aicc/callback",
        help="回调路径",
    )
    parser.add_argument("--record-id", default=str(int(time.time())), help="自检通话记录 ID")
    parser.add_argument("--repeat", type=int, default=1, help="POST 次数，用于验证幂等")
    args = parser.parse_args()

    token = get_env("AICC_CALLBACK_TOKEN").strip()
    aes_key = get_env("AICC_CALLBACK_AES_KEY").strip()
    if not token or not aes_key:
        print("缺少 AICC_CALLBACK_TOKEN / AICC_CALLBACK_AES_KEY，请先在 .env 中配置")
        return 1

    try:
        crypto = CallbackCrypto(token, aes_key)
    except Exception as exc:  # noqa: BLE001
        print(f"回调密钥配置错误：{exc}")
        return 1

    base = args.base_url.rstrip("/")
    path = args.path if args.path.startswith("/") else "/" + args.path
    payload = build_payload(str(args.record_id))

    print(f"目标：{base}{path}")
    print(f"记录：{args.record_id}，POST {max(1, args.repeat)} 次")

    with httpx.Client(timeout=15, base_url=base) as client:
        if not check_get(client, path, crypto):
            print("GET 验证未通过，后续 POST 不再执行")
            return 1
        for index in range(max(1, args.repeat)):
            if not post_once(client, path, crypto, payload):
                return 1
            if index + 1 < max(1, args.repeat):
                time.sleep(0.2)

    print("\n[数据库核对]")
    try:
        row = get_call_record(str(args.record_id))
        total = count_call_records(str(args.record_id))
    except Exception as exc:  # noqa: BLE001
        print(f"数据库不可用，跳过核对：{exc}")
        print("\n结论：HTTP 链路通过，入库未核对")
        return 0

    if not row:
        print("未查到记录，入库失败")
        return 1

    print(f"- 行数：{total}（幂等应为 1）")
    print(f"- 企业：{row.get('credit_code')}  客户：{row.get('customer_name')}")
    print(f"- 号码：{row.get('customer_phone')}  状态：{row.get('call_status')}  时长：{row.get('duration')}s")
    print(f"- 意向：{row.get('intent_level_tag_id')}/{row.get('intent_level_code')} {row.get('supplier_level')}")
    print(f"- 录音过期：{row.get('record_expire_at')}")
    transcript = row.get("asr_transcript") or ""
    print(f"- 转写 {len(transcript.splitlines())} 行：")
    for line in transcript.splitlines()[:4]:
        print(f"    {line}")

    ok = total == 1 and bool(transcript) and row.get("call_status") == "ANSWERED"
    print("\n结论：", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
