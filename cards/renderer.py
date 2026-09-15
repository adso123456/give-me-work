"""通知卡片渲染协议。"""

from __future__ import annotations

from typing import Protocol

from .models import JobNotificationCard


class CardRenderer(Protocol):
    def render(self, card: JobNotificationCard, token: str) -> str:
        """将卡片渲染为 AstrBot 可发送的消息文本。"""
