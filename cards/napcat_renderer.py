"""NapCat / QQ 渲染器。

QQ 文本消息不解析 Markdown，因此这里只输出纯文本（不输出 ** 等标记）。
保留独立类是为了将来接入 QQ 原生按钮卡片。
"""

from __future__ import annotations

from .models import JobNotificationCard
from .text_renderer import TextCardRenderer


class NapCatCardRenderer:
    def __init__(self) -> None:
        self._fallback = TextCardRenderer()

    def render(self, card: JobNotificationCard, token: str) -> str:
        return self._fallback.render(card, token)
