"""校验导出 txt 是否逐字来自原始 JSON（不生成、不润色）。

用法: python scripts/verify_asr.py 11541713298
"""

import json
import pathlib
import re
import sys

rid = sys.argv[1] if len(sys.argv) > 1 else "11541713298"
raw = json.loads(pathlib.Path(f"state/probe/asr/{rid}.json").read_text(encoding="utf-8"))
rounds = raw["data"]["callDetailList"]
texts = [(r.get("text") or "").strip() for r in rounds]

txt = pathlib.Path(f"state/asr_out/{rid}_未知.txt").read_text(encoding="utf-8")
lines = [
    l for l in txt.splitlines()
    if (l.startswith("机器人:") or l.startswith("客户:") or l.startswith("客户 [")) and "电话:" not in l
]

miss = []
for l in lines:
    body = re.sub(r"^[^:]+(?: \[[^\]]+\])?: ", "", l).strip()
    if body not in texts:
        miss.append(body)

print(f"JSON callDetailList: {len(rounds)} 条 | txt 对话行: {len(lines)} 行")
print(f"逐行命中 JSON text 字段: {len(lines) - len(miss)}/{len(lines)}")
if miss:
    print("不一致内容:", miss[:5])
else:
    print("✅ 全部一致：txt 没有任何生成/改写内容")
