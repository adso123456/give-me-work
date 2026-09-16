"""卡片动作编号解析（回复 1/2/3 执行对应动作）。"""

from __future__ import annotations

import re

INDEX_PATTERN = re.compile(r"^\s*([1-9])\s*$")


def parse_action_index(text: str, actions: list[str]) -> str | None:
    """把「1」「2」这样的回复解析成动作名；无法解析时返回 None。"""
    match = INDEX_PATTERN.match(text or "")
    if not match:
        return None
    index = int(match.group(1)) - 1
    if 0 <= index < len(actions):
        return str(actions[index])
    return None
