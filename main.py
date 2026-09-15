# -*- coding: utf-8 -*-
"""AstrBot 求职管理插件入口。"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain

from .agent import AgentResult, JobAgentService
from .cards.models import JobNotificationCard
from .cards.napcat_renderer import NapCatCardRenderer
from .cards.text_renderer import TextCardRenderer
from .events.mock import MockEventSource
from .events.server import EventServer
from .feishu.tools import FeishuTools
from .feishu.web_adapter import FeishuAdapterError, FeishuWebAdapter
from .models import CardAction, RecruitEvent
from .state_store import StateStore


PLUGIN_NAME = "astrbot_plugin_job_agent"
PLUGIN_VERSION = "0.1.0"


class _ProviderLlmClient:
    def __init__(self, provider: Any):
        self.provider = provider

    async def complete(self, prompt: str, system_prompt: str) -> str:
        response = await self.provider.text_chat(
            prompt=prompt,
            session_id=None,
            system_prompt=system_prompt,
        )
        return str(getattr(response, "completion_text", "") or "")


class _UnavailableLlm:
    async def complete(self, prompt: str, system_prompt: str) -> str:
        return '{"type":"final","summary":"未找到可用的 LLM Provider","card_type":"system"}'


class _MissingFeishuAdapter:
    async def _fail(self):
        raise FeishuAdapterError("未配置 feishu_table_url")

    async def search_records(self, query):
        return await self._fail()

    async def get_record(self, record_id):
        return await self._fail()

    async def create_record(self, fields):
        return await self._fail()

    async def update_record(self, record_id, fields):
        return await self._fail()

    async def list_records(self, limit=None):
        return await self._fail()

    async def check_access(self):
        return await self._fail()

    async def close(self):
        return None


@register(
    PLUGIN_NAME,
    "adso",
    "求职管理 Agent：维护飞书投递记录并通过 QQ 推送招聘通知",
    PLUGIN_VERSION,
)
class JobAgentPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.state: StateStore | None = None
        self.adapter: FeishuWebAdapter | _MissingFeishuAdapter | None = None
        self.tools: FeishuTools | None = None
        self.agent: JobAgentService | None = None
        self.event_server: EventServer | None = None
        self._provider = None
        self._data_dir = self._resolve_data_dir()

    async def initialize(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.state = StateStore(self._data_dir / "job_agent_state.json")
        await self.state.load()

        table_url = str(self._cfg("feishu_table_url", "") or "").strip()
        if table_url:
            self.adapter = FeishuWebAdapter(
                table_url,
                headless=bool(self._cfg("browser_headless", True)),
                debug_dir=self._data_dir / "debug",
                storage_state_path=self._data_dir / "feishu_storage_state.json",
            )
        else:
            self.adapter = _MissingFeishuAdapter()
        self.tools = FeishuTools(self.adapter)
        self._provider = self._resolve_provider()
        self.agent = JobAgentService(
            llm=_ProviderLlmClient(self._provider) if self._provider else _UnavailableLlm(),
            tools=self.tools,
        )

        token = str(self._cfg("webhook_token", "") or "").strip()
        if token:
            self.event_server = EventServer(
                token=token,
                state_store=self.state,
                on_event=self._handle_webhook_event,
                host=str(self._cfg("webhook_host", "0.0.0.0")),
                port=int(self._cfg("webhook_port", 6190)),
            )
            await self.event_server.start()
            logger.info("[job-agent] webhook started on port %s", self._cfg("webhook_port", 6190))
        logger.info("[job-agent] plugin initialized")

    async def terminate(self):
        if self.event_server:
            await self.event_server.stop()
        if self.adapter:
            await self.adapter.close()

    @filter.command("job_bind")
    async def job_bind(self, event: AstrMessageEvent):
        assert self.state is not None
        await self.state.bind_umo(event.unified_msg_origin)
        yield event.plain_result("✅ 已绑定当前会话为求职通知会话")

    @filter.command("job_status")
    async def job_status(self, event: AstrMessageEvent):
        assert self.state is not None
        access = "未检查"
        if self.adapter:
            try:
                access = "可访问" if await self.adapter.check_access() else "不可访问"
            except Exception as exc:
                access = f"不可访问（{self._short_error(exc)}）"
        provider = "已配置" if self._provider else "未找到"
        webhook = "运行中" if self.event_server else "未启用（缺少 webhook_token）"
        pending = await self.state.pending_card_count()
        yield event.plain_result(
            "\n".join(
                [
                    f"插件状态：运行中 v{PLUGIN_VERSION}",
                    f"飞书是否可访问：{access}",
                    f"LLM Provider：{provider}",
                    f"Webhook 状态：{webhook}",
                    f"已绑定 UMO：{self.state.bound_umo or '未绑定'}",
                    f"待处理卡片数：{pending}",
                ]
            )
        )

    @filter.command("job_table_test")
    async def job_table_test(self, event: AstrMessageEvent):
        try:
            assert self.adapter is not None
            records = await self.adapter.list_records(limit=3)
            lines = [f"✅ 飞书表格可访问，读取到 {len(records)} 条记录（最多显示 3 条）"]
            for record in records:
                lines.append(f"- {record.get('record_id')}: {self._record_summary(record)}")
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            yield event.plain_result(f"❌ 飞书表格测试失败：{self._short_error(exc)}")

    @filter.command("job_test_hr")
    async def job_test_hr(self, event: AstrMessageEvent):
        mock = MockEventSource.hr_message(event_id=f"evt_test_hr_{time.time_ns()}")
        result = await self._handle_event(mock, event.unified_msg_origin)
        yield event.plain_result(self._result_text(result))

    @filter.command("job_test_resume")
    async def job_test_resume(self, event: AstrMessageEvent):
        mock = MockEventSource.resume_request(event_id=f"evt_test_resume_{time.time_ns()}")
        result = await self._handle_event(mock, event.unified_msg_origin)
        yield event.plain_result(self._result_text(result))

    @filter.command("job_action")
    async def job_action(self, event: AstrMessageEvent):
        assert self.state is not None and self.agent is not None
        parts = event.message_str.strip().lstrip("/").split()
        if len(parts) < 3:
            yield event.plain_result("用法：/job_action <token> <action>")
            return
        token, action_name = parts[1], parts[2]
        pending = await self.state.get_pending_card_by_token(token)
        if pending is None:
            yield event.plain_result("❌ 未找到待处理卡片，token 可能已失效")
            return
        try:
            action = CardAction(
                action_id=f"action_{time.time_ns()}",
                card_id=str(pending.get("card_id") or ""),
                event_id=str(pending.get("event_id") or ""),
                record_id=pending.get("record_id"),
                action=action_name,
            )
            result = await self.agent.handle_action(action)
            if result.error:
                yield event.plain_result(f"❌ 未同步飞书：{result.summary}")
                return
            await self.state.complete_card(token)
            yield event.plain_result(f"✅ 已同步飞书\n{result.summary}")
        except Exception as exc:
            yield event.plain_result(f"❌ 未同步飞书：{self._short_error(exc)}")

    @filter.command("job")
    async def job_command(self, event: AstrMessageEvent):
        assert self.agent is not None
        command = event.message_str.strip()
        if command.startswith("/job"):
            command = command[4:].strip()
        if not command:
            yield event.plain_result("用法：/job <自然语言求职记录或查询>")
            return
        result = await self.agent.handle_command(command)
        yield event.plain_result(self._result_text(result))

    async def _handle_webhook_event(self, event: RecruitEvent):
        target = self.state.bound_umo if self.state else None
        await self._handle_event(event, target)

    async def _handle_event(self, event: RecruitEvent, target_umo: str | None) -> AgentResult:
        assert self.agent is not None and self.state is not None
        logger.info("[event] received %s type=%s", event.event_id, event.event_type)
        result = await self.agent.handle_event(event)
        if result.error:
            logger.warning("[event] agent failed %s error=%s", event.event_id, result.error)
            return result
        if not target_umo:
            result.error = "no_bound_umo"
            result.summary = "Agent 已处理，但尚未绑定通知会话"
            return result

        token = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        card = self._build_card(event, result)
        text = self._render_card(card, token)
        try:
            await self._send_text(target_umo, text)
            await self.state.add_pending_card(
                {
                    "card_id": card.card_id,
                    "event_id": event.event_id,
                    "record_id": result.record_id,
                    "token": token,
                    "umo": target_umo,
                }
            )
            logger.info("[card] send card=%s event=%s", card.card_id, event.event_id)
        except Exception as exc:
            logger.warning("[card] send failed event=%s error=%s", event.event_id, self._short_error(exc))
        logger.info("[event] completed %s", event.event_id)
        return result

    async def _send_text(self, umo: str, text: str) -> None:
        await self.context.send_message(umo, MessageChain(chain=[Plain(text)]))

    def _build_card(self, event: RecruitEvent, result: AgentResult) -> JobNotificationCard:
        card_type = result.card_type if result.card_type in {"hr_reply", "resume_request", "interview", "system"} else "system"
        action_map = {
            "hr_message": ["replied", "later"],
            "resume_request": ["resume_accepted", "resume_rejected", "later"],
            "interview_invitation": ["interview_confirmed", "later"],
            "system_notice": ["processed", "later"],
        }
        title_map = {
            "hr_message": "💬 您收到一条 HR 回复",
            "resume_request": "📄 HR 请求查看您的简历",
            "interview_invitation": "📅 您收到一条面试邀请",
            "system_notice": "📌 求职系统通知",
        }
        return JobNotificationCard(
            card_id=f"card_{secrets.token_hex(6)}",
            type=card_type,
            title=title_map.get(event.event_type, "📌 求职系统通知"),
            company=event.company,
            position=event.position,
            contact=event.contact,
            content=event.content,
            suggested_reply=result.suggested_reply or None,
            status_text=result.summary,
            actions=action_map.get(event.event_type, ["processed", "later"]),
        )

    def _render_card(self, card: JobNotificationCard, token: str) -> str:
        mode = str(self._cfg("card_mode", "auto") or "auto").lower()
        if mode in {"auto", "napcat"}:
            try:
                return NapCatCardRenderer().render(card, token)
            except Exception:
                pass
        return TextCardRenderer().render(card, token)

    def _resolve_provider(self):
        provider_id = str(self._cfg("provider_id", "") or "").strip()
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if provider:
                    return provider
            except Exception:
                logger.warning("[job-agent] configured provider unavailable")
        try:
            return self.context.get_using_provider()
        except Exception:
            return None

    def _resolve_data_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "plugin_data" / PLUGIN_NAME

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key)
        except Exception:
            value = None
        return default if value is None else value

    @staticmethod
    def _short_error(exc: Exception) -> str:
        return str(exc).replace("\n", " ")[:180] or type(exc).__name__

    @staticmethod
    def _record_summary(record: dict[str, Any]) -> str:
        fields = record.get("fields") or {}
        keys = ("公司名称", "应聘岗位", "投递状态", "面试日期")
        items = [f"{key}={fields[key]}" for key in keys if fields.get(key)]
        return ", ".join(items) or "字段为空"

    @staticmethod
    def _result_text(result: AgentResult) -> str:
        if result.error:
            return f"❌ {result.summary}"
        if result.suggested_reply:
            return f"{result.summary}\n建议回复：{result.suggested_reply}"
        return result.summary
