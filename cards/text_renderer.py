"""始终可用的纯文本卡片渲染器。

与图片卡片保持一致：动作既给出编号（可直接回复数字），也保留 /job_action 命令，
这样 Pillow 或中文字体缺失、回退成纯文本时功能不打折。
"""

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
            lines.extend(["", "回复数字即可同步飞书："])
            for index, action in enumerate(card.actions, start=1):
                label = ACTION_LABELS.get(action, action)
                lines.append(f"{index}. {label}")
            lines.append(f"（也可发送 /job_action {token} <动作>）")
        return "\n".join(lines)
