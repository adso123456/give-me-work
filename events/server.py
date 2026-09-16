"""带 Bearer Token 的招聘事件 HTTP 服务。

幂等语义：只有 `on_event` 成功返回后才标记事件已处理；
处理失败的事件保持"未处理"，发送方可安全重试。
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

try:
    from ..models import RecruitEvent
    from ..state_store import StateStore
except ImportError:  # 兼容直接运行测试文件
    from models import RecruitEvent
    from state_store import StateStore


logger = logging.getLogger(__name__)


class EventServer:
    def __init__(
        self,
        token: str,
        state_store: StateStore,
        on_event: Callable[[RecruitEvent], Awaitable[Any]],
        host: str = "127.0.0.1",
        port: int = 6190,
    ):
        self.token = token
        self.state_store = state_store
        self.on_event = on_event
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_post("/job-agent/events", self._handle)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._inflight: set[str] = set()

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._inflight.clear()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    async def _handle(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"status": "unauthorized"}, status=401)
        try:
            event = RecruitEvent.from_dict(await request.json())
        except (ValueError, TypeError, web.HTTPException) as exc:
            return web.json_response({"status": "invalid", "error": str(exc)}, status=400)

        if await self.state_store.is_event_processed(event.event_id):
            return web.json_response({"status": "duplicate", "event_id": event.event_id})
        if event.event_id in self._inflight:
            return web.json_response({"status": "in_progress", "event_id": event.event_id}, status=202)

        self._inflight.add(event.event_id)
        task = asyncio.create_task(self._dispatch(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.json_response(
            {"status": "accepted", "event_id": event.event_id}, status=202
        )

    async def _dispatch(self, event: RecruitEvent) -> None:
        try:
            await self.on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "[event] processing failed, event stays unprocessed: %s", event.event_id
            )
        else:
            await self.state_store.mark_event_processed(event.event_id)
        finally:
            self._inflight.discard(event.event_id)

    def _authorized(self, request: web.Request) -> bool:
        if not self.token:
            return False
        header = request.headers.get("Authorization") or ""
        expected = f"Bearer {self.token}"
        return hmac.compare_digest(header, expected)
