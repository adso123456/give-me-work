"""纯本地卡片图片渲染（Pillow），不依赖浏览器或外部 t2i 服务。

个人 QQ + NapCat 无法发送可点击按钮，因此卡片用图片呈现，
交互方式改为「回复数字」，并把编号印在卡片上。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:  # Pillow 缺失时由调用方回退到纯文本卡片
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore[assignment]

from .models import JobNotificationCard
from .text_renderer import ACTION_LABELS

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "C:/Windows/Fonts/msyh.ttc",
)

# 字体没有 emoji 字形，渲染前先去掉，避免出现豆腐块
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff"
    "\u2b00-\u2bff\ufe0f\u2764\u2600-\u26ff]"
)

BACKGROUND = (255, 255, 255)
BORDER = (222, 226, 230)
ACCENT = (51, 112, 255)
TITLE = (24, 28, 36)
TEXT = (45, 50, 60)
MUTED = (120, 128, 140)
ADVICE_BG = (241, 245, 255)


class CardImageRenderer:
    def __init__(self, width: int = 760, font_path: str | Path | None = None):
        self.width = int(width)
        self.font_path = Path(font_path) if font_path else self._detect_font()
        self._fonts: dict[int, Any] = {}

    # ------------------------------------------------------------------ 基础

    @staticmethod
    def _detect_font() -> Path | None:
        for candidate in FONT_CANDIDATES:
            path = Path(candidate)
            if path.exists():
                return path
        return None

    def available(self) -> bool:
        return (
            Image is not None
            and ImageDraw is not None
            and ImageFont is not None
            and self.font_path is not None
            and self.font_path.exists()
        )

    def _font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(str(self.font_path), size)
        return self._fonts[size]

    @staticmethod
    def strip_emoji(text: str) -> str:
        return _EMOJI.sub("", text or "").strip()

    @staticmethod
    def wrap(text: str, font, max_width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in (text or "").split("\n"):
            current = ""
            for char in paragraph:
                if font.getlength(current + char) <= max_width:
                    current += char
                    continue
                lines.append(current)
                current = char
            lines.append(current)
        return lines or [""]

    # ------------------------------------------------------------------ 渲染

    def render(
        self,
        card: JobNotificationCard,
        token: str,
        output_path: str | Path,
        minimal: bool = False,
    ) -> Path:
        """minimal=True 时只画「标题 + 消息内容」，
        建议回复与动作说明改用纯文本发送，方便用户复制。"""
        if not self.available():
            raise RuntimeError("图片卡片不可用：缺少 Pillow 或中文字体")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        pad = 30
        inner = self.width - pad * 2 - 20
        font_title = self._font(38)
        font_label = self._font(24)
        font_body = self._font(28)
        font_small = self._font(21)
        line_ratio = 1.42

        # (font, color, lines, gap_after, background)
        blocks: list[tuple[Any, tuple[int, int, int], list[str], int, Any]] = []

        def add(font, color, text: str, gap: int, background=None) -> None:
            wrap_width = inner - 30 if background is not None else inner
            lines = self.wrap(self.strip_emoji(text), font, wrap_width)
            blocks.append((font, color, lines, gap, background))

        add(font_title, TITLE, card.title, 18)
        if minimal:
            add(font_body, TEXT, card.content, 0)
        else:
            meta = "  ·  ".join(part for part in (card.company, card.position) if part)
            if meta:
                add(font_label, MUTED, meta, 12)
            if card.contact:
                add(font_label, MUTED, f"HR：{card.contact}", 16)
            add(font_label, MUTED, "对方消息", 6)
            add(font_body, TEXT, card.content, 20)
            if card.suggested_reply:
                add(font_label, ACCENT, "建议回复（仅为建议，未替你发送）", 8, ADVICE_BG)
                add(font_body, TEXT, card.suggested_reply, 28, ADVICE_BG)
            if card.status_text:
                add(font_small, MUTED, f"状态：{card.status_text}", 18)
            add(font_label, TEXT, "回复数字即可同步飞书：", 8)
            for index, action in enumerate(card.actions, start=1):
                add(font_body, ACCENT, f"{index}. {ACTION_LABELS.get(action, action)}", 6)
            add(font_small, MUTED, f"也可以发送 /job_action {token} <动作>", 0)

        height = pad * 2
        for font, _color, lines, gap, _bg in blocks:
            height += int(font.size * line_ratio) * len(lines) + gap
        height = max(height, 240)

        image = Image.new("RGB", (self.width, height), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [(6, 6), (self.width - 7, height - 7)], radius=18, outline=BORDER, width=2
        )
        draw.rounded_rectangle([(6, 10), (15, height - 11)], radius=5, fill=ACCENT)

        cursor = pad
        for font, color, lines, gap, background in blocks:
            line_height = int(font.size * line_ratio)
            block_height = line_height * len(lines)
            if background is not None:
                draw.rounded_rectangle(
                    [(pad - 14, cursor - 8), (self.width - pad + 14, cursor + block_height + 14)],
                    radius=10,
                    fill=background,
                )
            for line in lines:
                draw.text((pad + 10, cursor), line, font=font, fill=color)
                cursor += line_height
            cursor += gap
        image.save(target, "PNG")
        return target
