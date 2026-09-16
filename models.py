"""求职插件的轻量数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


EVENT_TYPES = {
    "hr_message",
    "resume_request",
    "interview_invitation",
    "system_notice",
    # 聊天流水：只推通知、不落表（由 main 里的 notify-only 分支处理）
    "chat_message",
}

CARD_ACTIONS = {
    "replied",
    "resume_accepted",
    "resume_rejected",
    "interview_confirmed",
    "processed",
    "later",
}


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO-8601 datetime") from exc
    else:
        raise ValueError("occurred_at must be a datetime or ISO-8601 string")
    if result.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return result


@dataclass(slots=True)
class RecruitEvent:
    schema_version: str
    event_id: str
    platform: str
    event_type: str
    conversation_id: str | None
    company: str | None
    position: str | None
    contact: str | None
    content: str
    occurred_at: datetime
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id is required")
        if not self.platform.strip():
            raise ValueError("platform is required")
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type}")
        if not self.content.strip():
            raise ValueError("content is required")
        self.occurred_at = _parse_datetime(self.occurred_at)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RecruitEvent":
        return cls(
            schema_version=str(payload.get("schema_version") or "1.0"),
            event_id=str(payload.get("event_id") or ""),
            platform=str(payload.get("platform") or ""),
            event_type=str(payload.get("event_type") or ""),
            conversation_id=payload.get("conversation_id"),
            company=payload.get("company"),
            position=payload.get("position"),
            contact=payload.get("contact"),
            content=str(payload.get("content") or ""),
            occurred_at=payload.get("occurred_at"),
            raw=payload.get("raw"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "platform": self.platform,
            "event_type": self.event_type,
            "conversation_id": self.conversation_id,
            "company": self.company,
            "position": self.position,
            "contact": self.contact,
            "content": self.content,
            "occurred_at": self.occurred_at.isoformat(),
            "raw": self.raw,
        }


@dataclass(slots=True)
class CardAction:
    action_id: str
    card_id: str
    event_id: str
    record_id: str | None
    action: str

    def __post_init__(self) -> None:
        for name in ("action_id", "card_id", "event_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.action not in CARD_ACTIONS:
            raise ValueError(f"unsupported action: {self.action}")


@dataclass(slots=True)
class JobRecord:
    record_id: str
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"record_id": self.record_id, "fields": dict(self.fields)}
