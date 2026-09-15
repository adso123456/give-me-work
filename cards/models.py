"""通知卡片的内部统一模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


CARD_TYPES = {"hr_reply", "resume_request", "interview", "system"}


@dataclass(slots=True)
class JobNotificationCard:
    card_id: str
    type: str
    title: str
    company: str | None
    position: str | None
    contact: str | None
    content: str
    suggested_reply: str | None
    status_text: str | None
    actions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.card_id.strip():
            raise ValueError("card_id is required")
        if self.type not in CARD_TYPES:
            raise ValueError(f"unsupported card type: {self.type}")
        if not self.title.strip() or not self.content.strip():
            raise ValueError("title and content are required")
