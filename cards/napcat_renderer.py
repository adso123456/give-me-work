"""NapCat 兼容渲染器。

V0.1 不依赖 QQ 原生按钮回调，使用 Markdown 风格文本并保留纯文本兼容性。
"""

from __future__ import annotations

from .models import JobNotificationCard
from .text_renderer import TextCardRenderer


class NapCatCardRenderer:
    def __init__(self) -> None:
        self._fallback = TextCardRenderer()

    def render(self, card: JobNotificationCard, token: str) -> str:
        try:
            text = self._fallback.render(card, token)
            return f"**{card.title}**\n{text[len(card.title):].lstrip()}"
        except Exception:
            return self._fallback.render(card, token)
