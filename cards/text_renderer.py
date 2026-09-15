"""始终可用的纯文本卡片渲染器。"""

from __future__ import annotations

from .models import JobNotificationCard


ACTION_LABELS = {
    "replied": "我已回复",
    "resume_accepted": "我已同意",
    "resume_rejected": "我未同意",
    "interview_confirmed": "我已确认面试",
    "processed": "已处理",
    "later": "稍后处理",
}


class TextCardRenderer:
    def render(self, card: JobNotificationCard, token: str) -> str:
        lines = [card.title]
        if card.company:
            lines.append(card.company)
        if card.position:
            lines.append(card.position)
        if card.contact:
            lines.append(f"HR：{card.contact}")
        lines.extend(["", "对方：", f"“{card.content}”"])
        if card.suggested_reply:
            lines.extend(["", "🤖 建议回复：", f"“{card.suggested_reply}”"])
        if card.status_text:
            lines.extend(["", f"当前：{card.status_text}"])
        if card.actions:
            lines.extend(["", "请在完成实际操作后选择："])
            for action in card.actions:
                label = ACTION_LABELS.get(action, action)
                lines.append(f"[{label}] /job_action {token} {action}")
        return "\n".join(lines)
