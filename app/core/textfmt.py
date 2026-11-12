"""终端文本对齐工具：中文按 2 个字符宽度计算。"""

from __future__ import annotations

import unicodedata


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def truncate(text: str, max_width: int) -> str:
    result = ""
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > max_width:
            break
        result += ch
        used += w
    return result


def pad(text: str, width: int) -> str:
    text = truncate(text, width)
    return text + " " * max(0, width - display_width(text))
