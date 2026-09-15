"""飞书首次扫码登录流程。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from .web_adapter import FeishuAdapterError


async def run_feishu_login(
    adapter: Any,
    send_qr: Callable[[Path], Awaitable[None]],
    qr_path: str | Path,
) -> str:
    """在没有有效登录态时发送二维码并等待扫码完成。"""
    try:
        if adapter.has_storage_state():
            try:
                if await adapter.check_access():
                    return "已有有效登录状态，无需扫码"
            except FeishuAdapterError:
                pass

        await adapter.start_qr_login(qr_path)
        await send_qr(Path(qr_path))
        await adapter.wait_for_qr_login()
        return "登录成功"
    finally:
        await adapter.close()
