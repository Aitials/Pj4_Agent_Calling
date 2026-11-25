"""离线自检：钉钉 AI 卡片流式控制器（不依赖网络/LLM/数据库）。

覆盖：
1. 建卡失败 → start 返回 False（调用方应回退文本）
2. 时间 + 字数双阈值节流；推送内容单调递增，收尾写入全文
3. 首字前的进度提示
4. 连续推流失败 → 放弃卡片，finish 返回 False（触发文本兜底）

用法：
    .venv\\Scripts\\python.exe scripts\\selftest_dingtalk_stream.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.channels.dingtalk import _AnswerCard  # noqa: E402

_failures: list[str] = []


def _check(name: str, ok: bool) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        _failures.append(name)


class FakeCard:
    """模拟 AIMarkdownCardInstance 的最小接口。"""

    def __init__(self, *, fail_start: bool = False, fail_stream_after: int | None = None):
        self.card_instance_id = ""
        self.pushed: list[str] = []
        self.finished: str | None = None
        self.failed = False
        self.last_stream_ok = True
        self.last_update_ok = True
        self._fail_start = fail_start
        self._fail_stream_after = fail_stream_after

    def set_title_and_logo(self, title: str, logo: str) -> None:
        return None

    def ai_start(self) -> None:
        if not self._fail_start:
            self.card_instance_id = "fake-card"

    def ai_streaming(self, text: str) -> None:
        if (
            self._fail_stream_after is not None
            and len(self.pushed) >= self._fail_stream_after
        ):
            self.last_stream_ok = False
            return
        self.last_stream_ok = True
        self.pushed.append(text)

    def ai_finish(self, text: str) -> None:
        self.finished = text
        self.last_update_ok = True

    def ai_fail(self) -> None:
        self.failed = True


def _card(fake: FakeCard, **kwargs) -> _AnswerCard:
    return _AnswerCard(
        None,
        None,
        card_factory=lambda _client, _incoming: fake,
        **kwargs,
    )


async def case_start_failure() -> None:
    fake = FakeCard(fail_start=True)
    card = _card(fake)
    _check("建卡失败 → start False", await card.start() is False)


async def case_throttle_and_finish() -> None:
    fake = FakeCard()
    card = _card(fake, min_interval=0.06, min_chars=4, hint_interval=30)
    started = await card.start()

    full = ""
    for _ in range(10):
        full += "字" * 3
        card.feed(full)
        await asyncio.sleep(0.02)

    await asyncio.sleep(0.1)
    ok = await card.finish(full)

    lengths = [len(item) for item in fake.pushed]
    _check("建卡成功 → start True", started)
    _check("至少推送一次中间态", len(fake.pushed) >= 1)
    _check("推送内容单调递增", lengths == sorted(lengths))
    _check("节流生效（未把所有中间态都推上去）", len(fake.pushed) < 10)
    _check("收尾写入全文", fake.finished == full)
    _check("收尾成功 → finish True", ok is True)


async def case_progress_hint() -> None:
    fake = FakeCard()
    card = _card(fake, min_interval=0.05, min_chars=1, hint_interval=0.5)
    await card.start()
    await asyncio.sleep(0.65)
    hint = any("正在思考" in item for item in fake.pushed)
    ok = await card.finish("最终内容")
    _check("首字前有进度提示", hint)
    _check("提示后仍可正常收尾", ok is True)


async def case_stream_failures() -> None:
    fake = FakeCard(fail_stream_after=0)
    card = _card(fake, min_interval=0.05, min_chars=1, hint_interval=30)
    await card.start()
    card.feed("一些内容" * 5)
    await asyncio.sleep(0.4)
    ok = await card.finish("一些内容" * 5)
    _check("连续推流失败 → finish False（走文本兜底）", ok is False)


async def main() -> int:
    await case_start_failure()
    await case_throttle_and_finish()
    await case_progress_hint()
    await case_stream_failures()

    if _failures:
        print(f"\n=== 失败 {len(_failures)} 项：{', '.join(_failures)} ===")
        return 1
    print("\n=== 全部通过 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop))
