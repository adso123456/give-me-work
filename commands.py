"""指令参数解析（唤醒前缀、命令名、可选 --yes 之类的标志）。"""

from __future__ import annotations

import re

# 视为"跳过二次确认"的标志
FORCE_FLAGS = {"--yes", "-y", "yes", "force", "--force", "确认", "强制"}


def parse_command_args(message_str: str, command: str) -> list[str] | None:
    """从消息里取出某个指令的参数。

    兼容三种写法（AstrBot 的唤醒前缀可能是 `/`、`!` 或空）：
        /job_delete rec1 rec2
        !job_delete rec1
        job_delete rec1
    不是该指令时返回 None。
    """
    text = (message_str or "").strip()
    if not text:
        return None
    pattern = re.compile(
        rf"^[^\w]*{re.escape(command)}(?:\s+(?P<args>.*))?$",
        flags=re.S,
    )
    match = pattern.match(text)
    if not match:
        return None
    args = (match.group("args") or "").strip()
    return args.split() if args else []


def parse_delete_args(message_str: str) -> tuple[list[str], bool] | None:
    """解析 /job_delete 的参数，返回 (记录ID列表, 是否强制删除)。

    不是 /job_delete 时返回 None（可能是 /job_delete_confirm 或别的指令）。
    """
    args = parse_command_args(message_str, "job_delete")
    if args is None:
        return None
    force = any(arg.casefold() in FORCE_FLAGS for arg in args)
    record_ids = [arg for arg in args if arg.casefold() not in FORCE_FLAGS]
    return record_ids, force


def parse_confirm_args(message_str: str) -> list[str] | None:
    """解析 /job_delete_confirm 的参数（删除确认 token）。"""
    return parse_command_args(message_str, "job_delete_confirm")


def parse_find_args(message_str: str) -> list[str] | None:
    """解析 /job_delete_find 的参数（搜索关键词）。"""
    return parse_command_args(message_str, "job_delete_find")
