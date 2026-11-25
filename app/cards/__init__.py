"""卡片层：按通道绑定卡片构造模块。

流程层统一 `from app.cards import cards`，启动时用 `configure_cards()`
绑定飞书或钉钉的构造模块；切通道不需要改 23 处 `cards.build_*`。
"""

from __future__ import annotations

_target = None


def configure_cards(module) -> None:
    """启动时绑定当前通道的卡片模块。"""
    global _target
    _target = module


def _get_cards():
    global _target
    if _target is None:
        from app.cards import aicc_task_cards as default

        _target = default
    return _target


class _CardsProxy:
    """把所有属性访问转发给当前绑定的卡片模块。"""

    def __getattr__(self, name):
        return getattr(_get_cards(), name)


cards = _CardsProxy()

__all__ = ["cards", "configure_cards"]

