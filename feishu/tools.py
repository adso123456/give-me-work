"""给 Job Agent 暴露的业务级飞书工具。"""

from __future__ import annotations

from typing import Any


class FeishuTools:
    tool_names = ("search_jobs", "get_job", "create_job", "update_job")

    def __init__(self, adapter: Any):
        self.adapter = adapter

    async def search_jobs(self, query: str) -> dict[str, Any]:
        return {"records": await self.adapter.search_records(query)}

    async def get_job(self, record_id: str) -> dict[str, Any] | None:
        return await self.adapter.get_record(record_id)

    async def create_job(self, fields: dict[str, Any]) -> dict[str, Any]:
        return await self.adapter.create_record(fields)

    async def update_job(self, record_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        return await self.adapter.update_record(record_id, fields)
