"""求职 Agent 的最小 JSON Tool Loop。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

try:
    from .models import CardAction, RecruitEvent
    from .prompts import SYSTEM_PROMPT, build_agent_prompt
except ImportError:  # 兼容直接运行测试文件
    from models import CardAction, RecruitEvent
    from prompts import SYSTEM_PROMPT, build_agent_prompt


class LlmClient(Protocol):
    async def complete(self, prompt: str, system_prompt: str) -> str:
        """调用 AstrBot 当前配置的 LLM Provider。"""


class FeishuToolClient(Protocol):
    async def search_jobs(self, query: str) -> Any: ...
    async def get_job(self, record_id: str) -> Any: ...
    async def create_job(self, fields: dict[str, Any]) -> Any: ...
    async def update_job(self, record_id: str, fields: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class AgentResult:
    summary: str
    suggested_reply: str = ""
    card_type: str = "system"
    record_id: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    steps: int = 0
    error: str | None = None


class JobAgentService:
    ALLOWED_TOOLS = {"search_jobs", "get_job", "create_job", "update_job"}

    def __init__(self, llm: LlmClient, tools: FeishuToolClient, max_steps: int = 6):
        self.llm = llm
        self.tools = tools
        self.max_steps = max_steps

    async def handle_event(self, event: RecruitEvent) -> AgentResult:
        return await self._run("recruit_event", event.to_dict())

    async def handle_action(self, action: CardAction) -> AgentResult:
        return await self._run(
            "card_action",
            {
                "action_id": action.action_id,
                "card_id": action.card_id,
                "event_id": action.event_id,
                "record_id": action.record_id,
                "action": action.action,
            },
        )

    async def handle_command(self, command: str) -> AgentResult:
        return await self._run("user_command", {"command": command})

    async def _run(self, kind: str, payload: dict[str, Any]) -> AgentResult:
        history: list[dict[str, Any]] = []
        record_id: str | None = payload.get("record_id")
        repaired = False

        for step in range(1, self.max_steps + 1):
            prompt = build_agent_prompt(kind, payload, history)
            raw = await self.llm.complete(prompt, SYSTEM_PROMPT)
            parsed = self._parse_json(raw)
            if parsed is None:
                if repaired:
                    return AgentResult(
                        summary="Agent 返回格式无效，未修改飞书",
                        record_id=record_id,
                        steps=step,
                        error="invalid_llm_json",
                    )
                repaired = True
                history.append({"error": "上次输出不是有效 JSON，请只输出一个 JSON 对象。"})
                continue

            result_type = parsed.get("type")
            if result_type == "tool_call":
                tool_name = parsed.get("tool")
                arguments = parsed.get("arguments") or {}
                if tool_name not in self.ALLOWED_TOOLS:
                    return AgentResult(
                        summary="Agent 请求了不允许的工具，未修改飞书",
                        record_id=record_id,
                        steps=step,
                        error="unsupported_tool",
                    )
                try:
                    tool_result = await self._call_tool(tool_name, arguments)
                except Exception as exc:
                    history.append({"tool": tool_name, "error": str(exc)[:300]})
                    continue
                record_id = self._extract_record_id(tool_result) or record_id
                history.append({"tool": tool_name, "arguments": arguments, "result": tool_result})
                continue

            if result_type == "final":
                return AgentResult(
                    summary=str(parsed.get("summary") or "已完成求职记录处理"),
                    suggested_reply=str(parsed.get("suggested_reply") or ""),
                    card_type=str(parsed.get("card_type") or "system"),
                    record_id=str(parsed.get("record_id") or record_id or "") or None,
                    fields=parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {},
                    steps=step,
                )

            history.append({"error": "type 必须是 tool_call 或 final。"})

        return AgentResult(
            summary="Agent 达到最大步骤数，未确认最终结果",
            record_id=record_id,
            steps=self.max_steps,
            error="max_steps",
        )

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        method = getattr(self.tools, name)
        if name == "search_jobs":
            return await method(str(arguments.get("query") or ""))
        if name == "get_job":
            return await method(str(arguments.get("record_id") or ""))
        if name == "create_job":
            return await method(arguments.get("fields") or {})
        return await method(
            str(arguments.get("record_id") or ""),
            arguments.get("fields") or {},
        )

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        candidate = (text or "").strip()
        if candidate.startswith("```"):
            candidate = candidate.strip("`").strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            start = candidate.find("{")
            if start < 0:
                return None
            try:
                parsed, _ = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _extract_record_id(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None
        if result.get("record_id"):
            return str(result["record_id"])
        records = result.get("records")
        if isinstance(records, list) and records:
            first = records[0]
            if isinstance(first, dict) and first.get("record_id"):
                return str(first["record_id"])
        return None
