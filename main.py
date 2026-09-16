# -*- coding: utf-8 -*-
"""AstrBot 求职管理插件入口。

存储层使用飞书开放平台多维表格 API（tenant_access_token），
不再依赖 Playwright 抓取网页（原方案无法读写 canvas 网格）。
"""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from .agent import AgentResult, JobAgentService
from .cards.actions import parse_action_index
from .cards.image_renderer import CardImageRenderer
from .cards.models import JobNotificationCard
from .cards.napcat_renderer import NapCatCardRenderer
from .cards.text_renderer import ACTION_LABELS, TextCardRenderer
from .commands import parse_confirm_args, parse_delete_args, parse_find_args
from .events.mock import MockEventSource
from .events.server import EventServer
from .feishu.api_adapter import FeishuAdapterError, FeishuApiAdapter, parse_table_url
from .feishu.tools import FeishuTools
from .feishu.write_test import run_table_write_test
from .models import CardAction, RecruitEvent
from .state_store import StateStore

try:  # AstrBot 4.27+ 提供，用于把数据写到 data/plugin_data/<plugin>
    from astrbot.api.star import StarTools
except Exception:  # pragma: no cover - 兼容旧版本
    StarTools = None  # type: ignore[assignment]


PLUGIN_NAME = "astrbot_plugin_job_agent"
PLUGIN_VERSION = "0.4.0"


class _ProviderLlmClient:
    """把 AstrBot Provider 包成 Agent 需要的 complete(prompt, system_prompt) 接口。"""

    def __init__(self, provider: Any):
        self.provider = provider

    async def complete(self, prompt: str, system_prompt: str) -> str:
        response = await self.provider.text_chat(
            prompt=prompt,
            session_id=None,
            system_prompt=system_prompt,
        )
        return str(getattr(response, "completion_text", "") or "")


class _MissingFeishuAdapter:
    """未配置飞书 API 时的占位实现：所有操作返回可读错误，不假装成功。"""

    def __init__(self, reason: str = "未配置飞书应用凭证"):
        self.reason = reason

    async def _fail(self):
        raise FeishuAdapterError(self.reason)

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

    async def list_fields(self, refresh: bool = False):
        return await self._fail()

    async def field_names(self):
        return await self._fail()

    async def delete_record(self, record_id):
        return await self._fail()

    async def delete_records(self, record_ids):
        return await self._fail()

    async def close(self):
        return None


@register(
    PLUGIN_NAME,
    "adso",
    "求职管理 Agent：维护飞书投递记录并通过 QQ 推送招聘通知（开放平台 API 版）",
    PLUGIN_VERSION,
)
class JobAgentPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.state: StateStore | None = None
        self.adapter: Any = None
        self.tools: FeishuTools | None = None
        self.agent: JobAgentService | None = None
        self.event_server: EventServer | None = None
        self._provider = None
        self._data_dir = self._resolve_data_dir()
        self._adapter_reason = ""

    # ------------------------------------------------------------ 生命周期

    async def initialize(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_state()
        self.state = StateStore(self._data_dir / "job_agent_state.json")
        await self.state.load()

        self.adapter = self._build_adapter()
        self.tools = FeishuTools(self.adapter)

        self._provider = None
        self.agent = None
        if await self._ensure_agent() is None:
            logger.warning(
                "[job-agent] 初始化时未解析到 LLM Provider，将在首次调用时重试（可在插件配置里填写 provider_id）"
            )

        token = str(self._cfg("webhook_token", "") or "").strip()
        if token:
            self.event_server = EventServer(
                token=token,
                state_store=self.state,
                on_event=self._handle_webhook_event,
                host=str(self._cfg("webhook_host", "127.0.0.1")),
                port=int(self._cfg("webhook_port", 6190)),
            )
            await self.event_server.start()
            logger.info(
                "[job-agent] webhook started on %s:%s",
                self._cfg("webhook_host", "127.0.0.1"),
                self._cfg("webhook_port", 6190),
            )
        else:
            logger.info("[job-agent] webhook 未启用（webhook_token 为空）")
        logger.info(
            "[job-agent] plugin initialized (data=%s, feishu=%s, llm=%s)",
            self._data_dir,
            "api" if isinstance(self.adapter, FeishuApiAdapter) else "未配置",
            "ok" if self.agent else "缺失",
        )

    async def terminate(self):
        if self.event_server:
            await self.event_server.stop()
        if self.adapter:
            await self.adapter.close()

    # ------------------------------------------------------------ 指令

    @filter.command("job_bind")
    async def job_bind(self, event: AstrMessageEvent):
        assert self.state is not None
        await self.state.bind_umo(event.unified_msg_origin)
        yield event.plain_result("✅ 已绑定当前会话为求职通知会话")

    @filter.command("job_status")
    async def job_status(self, event: AstrMessageEvent):
        assert self.state is not None
        access = "未配置"
        fields = "—"
        if isinstance(self.adapter, FeishuApiAdapter):
            try:
                names = await self.adapter.field_names()
                access = "可访问"
                fields = f"{len(names)} 个：{'、'.join(names)}"
            except Exception as exc:
                access = f"不可访问（{self._short_error(exc)}）"
        elif self.adapter is not None:
            access = f"不可访问（{self._short_error(FeishuAdapterError(self._adapter_reason))}）"
        provider = "已配置" if await self._ensure_agent() is not None else "未找到（Agent 不可用）"
        webhook = "运行中" if self.event_server else "未启用（缺少 webhook_token）"
        pending = await self.state.pending_card_count()
        yield event.plain_result(
            "\n".join(
                [
                    f"插件状态：运行中 v{PLUGIN_VERSION}（飞书开放平台 API）",
                    f"飞书表格：{access}",
                    f"表格字段：{fields}",
                    f"LLM Provider：{provider}",
                    f"Webhook 状态：{webhook}",
                    f"已绑定 UMO：{self.state.bound_umo or '未绑定'}",
                    f"待处理卡片数：{pending}",
                ]
            )
        )

    @filter.command("job_feishu_login")
    async def job_feishu_login(self, event: AstrMessageEvent):
        yield event.plain_result(
            "ℹ️ 当前使用飞书开放平台 API，无需扫码登录。\n"
            "如提示权限不足(91403)：请在开放平台给应用加 bitable:app 权限并发布版本，"
            "再把该多维表格添加为应用可编辑的文档。"
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

    @filter.command("job_table_write_test")
    async def job_table_write_test(self, event: AstrMessageEvent):
        try:
            assert self.adapter is not None
            yield event.plain_result(await run_table_write_test(self.adapter))
        except Exception as exc:
            yield event.plain_result(f"❌ 写入测试执行失败：{self._short_error(exc)}")

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
        parts = event.message_str.strip().lstrip("/").split()
        if len(parts) < 3:
            yield event.plain_result("用法：/job_action <token> <action>")
            return
        yield event.plain_result(await self._apply_card_action(parts[1], parts[2]))

    @filter.regex(r"^\s*[1-9]\s*$")
    async def job_quick_action(self, event: AstrMessageEvent):
        """回复卡片上的数字即可执行动作（只有存在待处理卡片时才拦截）。"""
        if self.state is None:
            return
        pending = await self.state.latest_pending_card(event.unified_msg_origin)
        if pending is None:
            return
        action_name = parse_action_index(event.message_str, list(pending.get("actions") or []))
        if action_name is None:
            return
        event.stop_event()
        token = str(pending.get("token") or "")
        yield event.plain_result(await self._apply_card_action(token, action_name))

    @filter.command("job")
    async def job_command(self, event: AstrMessageEvent):
        missing = await self._missing_agent()
        if missing is not None:
            yield event.plain_result(self._result_text(missing))
            return
        command = event.message_str.strip()
        if command.startswith("/job"):
            command = command[4:].strip()
        if not command:
            yield event.plain_result("用法：/job <自然语言求职记录或查询>")
            return
        result = await self.agent.handle_command(command)
        yield event.plain_result(self._result_text(result))

    # ------------------------------------------------------------ 删除指令

    @filter.command("job_delete")
    async def job_delete(self, event: AstrMessageEvent):
        parsed = parse_delete_args(event.message_str)
        record_ids, force = parsed if parsed is not None else ([], False)
        if not record_ids:
            yield event.plain_result(
                "用法：/job_delete <记录ID> [更多记录ID…] [--yes]\n"
                "· 默认先预览、再让你确认，不会立刻删\n"
                "· 加 --yes 直接删除（不可撤销）\n"
                "· 不知道记录ID：先 /job_delete_find <关键词>"
            )
            return
        yield event.plain_result(
            await self._request_delete(record_ids, force, event.unified_msg_origin)
        )

    @filter.command("job_delete_confirm")
    async def job_delete_confirm(self, event: AstrMessageEvent):
        args = parse_confirm_args(event.message_str) or []
        if not args:
            yield event.plain_result("用法：/job_delete_confirm <确认码>")
            return
        yield event.plain_result(await self._confirm_delete(args[0]))

    @filter.command("job_delete_find")
    async def job_delete_find(self, event: AstrMessageEvent):
        args = parse_find_args(event.message_str) or []
        keyword = " ".join(args).strip()
        yield event.plain_result(await self._find_for_delete(keyword))

    # ------------------------------------------------------------ 事件处理

    async def _handle_webhook_event(self, event: RecruitEvent):
        target = self.state.bound_umo if self.state else None
        if not target:
            logger.warning("[event] %s 收到但尚未绑定通知会话", event.event_id)
            raise RuntimeError("no_bound_umo")
        await self._handle_event(event, target)

    async def _handle_event(self, event: RecruitEvent, target_umo: str | None) -> AgentResult:
        logger.info("[event] received %s type=%s", event.event_id, event.event_type)
        missing = await self._missing_agent()
        if missing is not None:
            return missing
        assert self.agent is not None and self.state is not None
        result = await self.agent.handle_event(event)
        if result.error:
            logger.warning("[event] agent failed %s error=%s", event.event_id, result.error)
            if target_umo:
                await self._safe_send(target_umo, f"⚠️ 招聘事件处理失败：{result.summary}")
            return result
        if not target_umo:
            result.error = "no_bound_umo"
            result.summary = "Agent 已处理，但尚未绑定通知会话"
            return result

        token = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        card = self._build_card(event, result)
        try:
            await self._send_card(target_umo, card, token)
            await self.state.add_pending_card(
                {
                    "card_id": card.card_id,
                    "event_id": event.event_id,
                    "record_id": result.record_id,
                    "token": token,
                    "umo": target_umo,
                    "actions": list(card.actions),
                }
            )
            logger.info("[card] send card=%s event=%s", card.card_id, event.event_id)
        except Exception as exc:
            logger.warning("[card] send failed event=%s error=%s", event.event_id, self._short_error(exc))
            if target_umo:
                await self._safe_send(target_umo, f"⚠️ 通知卡片发送失败：{self._short_error(exc)}")
        logger.info("[event] completed %s", event.event_id)
        return result

    async def _apply_card_action(self, token: str, action_name: str) -> str:
        assert self.state is not None
        missing = await self._missing_agent()
        if missing is not None:
            return self._result_text(missing)
        pending = await self.state.get_pending_card_by_token(token)
        if pending is None:
            return "❌ 未找到待处理卡片，token 可能已失效"
        if str(pending.get("status") or "pending") != "pending":
            return "ℹ️ 这张卡片已经处理过了，未重复执行"
        allowed = [str(item) for item in (pending.get("actions") or [])]
        if allowed and action_name not in allowed:
            return f"❌ 这张卡片只支持：{'、'.join(allowed)}"
        assert self.agent is not None
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
                return f"❌ 未同步飞书：{result.summary}"
            await self.state.complete_card(token)
            if result.suggested_reply:
                return f"✅ 已同步飞书\n{result.summary}\n建议回复：{result.suggested_reply}"
            return f"✅ 已同步飞书\n{result.summary}"
        except Exception as exc:
            return f"❌ 未同步飞书：{self._short_error(exc)}"

    async def _send_card(self, umo: str, card: JobNotificationCard, token: str) -> None:
        mode = str(self._cfg("card_mode", "auto") or "auto").lower()
        image_path: Path | None = None
        if mode in {"auto", "image", "napcat"}:
            try:
                image_path = self._render_card_image(card, token)
            except Exception as exc:
                logger.warning("[card] 图片卡片渲染失败，回退纯文本：%s", self._short_error(exc))
        if image_path is not None:
            chain = MessageChain(
                chain=[
                    Image.fromFileSystem(str(image_path)),
                    Plain(self._card_text(card, token)),
                ]
            )
        else:
            chain = MessageChain(chain=[Plain(self._render_card(card, token))])
        await self.context.send_message(umo, chain)

    def _render_card_image(self, card: JobNotificationCard, token: str) -> Path:
        renderer = CardImageRenderer()
        if not renderer.available():
            raise RuntimeError("缺少 Pillow 或中文字体")
        cards_dir = self._data_dir / "cards"
        # 图片只保留「标题 + 消息内容」，其余用文本发，方便复制建议回复
        path = renderer.render(card, token, cards_dir / f"{card.card_id}.png", minimal=True)
        self._prune_card_images(cards_dir)
        return path

    @staticmethod
    def _prune_card_images(cards_dir: Path, keep: int = 50) -> None:
        try:
            files = sorted(cards_dir.glob("card_*.png"), key=lambda item: item.stat().st_mtime)
        except OSError:
            return
        for stale in files[:-keep] if len(files) > keep else []:
            try:
                stale.unlink()
            except OSError:
                continue

    @staticmethod
    def _card_text(card: JobNotificationCard, token: str) -> str:
        """跟随图片发送的纯文本部分：建议回复 + 动作指令（可直接复制）。"""
        lines: list[str] = []
        meta = "  ·  ".join(part for part in (card.company, card.position) if part)
        if meta:
            lines.append(meta)
        if card.contact:
            lines.append(f"HR：{card.contact}")
        if card.suggested_reply:
            lines.extend(["", "🤖 建议回复（复制即用，未替你发送）：", f"“{card.suggested_reply}”"])
        if card.status_text:
            lines.extend(["", f"当前：{card.status_text}"])
        lines.extend(["", "回复数字即可同步飞书："])
        for index, action in enumerate(card.actions, start=1):
            lines.append(f"{index}. {ACTION_LABELS.get(action, action)}")
        lines.append(f"（也可发送 /job_action {token} <动作>）")
        return "\n".join(lines)

    async def _safe_send(self, umo: str, text: str) -> None:
        try:
            await self._send_text(umo, text)
        except Exception as exc:  # 通知失败不应再抛异常打断主流程
            logger.warning("[card] fallback notify failed error=%s", self._short_error(exc))

    def _delete_adapter(self):
        adapter = self.adapter
        if adapter is None or not hasattr(adapter, "delete_records"):
            raise FeishuAdapterError("当前飞书适配器不支持删除")
        return adapter

    async def _request_delete(self, record_ids: list[str], force: bool, umo: str) -> str:
        """删除入口：默认先预览并要求二次确认，--yes 才直接删。"""
        assert self.state is not None
        adapter = self._delete_adapter()
        try:
            records = []
            missing = []
            for record_id in record_ids:
                record = await adapter.get_record(record_id)
                if record is None:
                    missing.append(record_id)
                else:
                    records.append(record)
        except Exception as exc:
            return f"❌ 查询待删记录失败：{self._short_error(exc)}"

        lines: list[str] = []
        if missing:
            lines.append("❌ 未找到记录：" + "、".join(str(item) for item in missing))
        if not records:
            return "\n".join(lines) or "❌ 没有可删除的记录"

        lines.append(f"⚠️ 即将删除 {len(records)} 条记录：")
        for record in records:
            lines.append(f"· {record.get('record_id')}：{self._record_summary(record)}")
        ids = [str(record.get("record_id")) for record in records]

        if force or not bool(self._cfg("delete_confirm_required", True)):
            try:
                deleted = await adapter.delete_records(ids)
            except Exception as exc:
                return "\n".join(lines + [f"❌ 删除失败：{self._short_error(exc)}"])
            lines.append(f"🗑️ 已删除 {len(deleted)} 条记录（不可撤销）")
            return "\n".join(lines)

        await self.state.prune_deletes()
        token = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:8]
        await self.state.add_pending_delete(
            {"token": token, "record_ids": ids, "umo": umo}
        )
        lines.append("")
        lines.append(f"确认删除请发送：/job_delete_confirm {token}")
        lines.append("（5 分钟内有效；不确认就什么都不会删）")
        return "\n".join(lines)

    async def _confirm_delete(self, token: str) -> str:
        assert self.state is not None
        await self.state.prune_deletes()
        pending = await self.state.get_pending_delete(token)
        if pending is None:
            return "❌ 确认码无效或已过期（有效期 5 分钟），请重新发起 /job_delete"
        if str(pending.get("status") or "pending") != "pending":
            return "ℹ️ 这次删除已经执行过了，未重复删除"
        adapter = self._delete_adapter()
        record_ids = [str(item) for item in (pending.get("record_ids") or [])]
        try:
            deleted = await adapter.delete_records(record_ids)
        except Exception as exc:
            return f"❌ 删除失败：{self._short_error(exc)}"
        await self.state.complete_delete(token)
        return "🗑️ 已删除 %d 条记录（不可撤销）：%s" % (
            len(deleted),
            "、".join(deleted) or "（无）",
        )

    async def _find_for_delete(self, keyword: str) -> str:
        adapter = self._delete_adapter()
        try:
            records = await adapter.search_records(keyword)
        except Exception as exc:
            return f"❌ 查询失败：{self._short_error(exc)}"
        if not records:
            return f"没有匹配「{keyword}」的记录" if keyword else "表格里没有记录"
        lines = [f"🔍 匹配 {len(records)} 条（最多显示 5 条）："]
        for record in records[:5]:
            lines.append(f"· {record.get('record_id')}：{self._record_summary(record)}")
        ids = " ".join(str(record.get("record_id")) for record in records[:5])
        lines.extend(["", "删除其中一条：/job_delete <记录ID>", f"删除这 5 条：/job_delete {ids}"])
        return "\n".join(lines)

    async def _send_text(self, umo: str, text: str) -> None:
        await self.context.send_message(umo, MessageChain(chain=[Plain(text)]))

    # ------------------------------------------------------------ 内部工具

    def _missing_agent_sync_note(self) -> AgentResult:
        return AgentResult(
            summary="未配置可用的 LLM Provider（请在 AstrBot 中配置模型，或在插件配置里填写 provider_id）",
            error="no_llm",
        )

    async def _missing_agent(self) -> AgentResult | None:
        if await self._ensure_agent() is not None:
            return None
        return self._missing_agent_sync_note()

    async def _ensure_agent(self) -> JobAgentService | None:
        """懒解析 Provider：插件加载可能早于 Provider 注册，或用户稍后才配置模型。"""
        if self.agent is not None:
            return self.agent
        if self.tools is None:
            return None
        self._provider = await self._resolve_provider()
        if self._provider is None:
            return None
        logger.info("[job-agent] 已解析到 LLM Provider，Agent 就绪")
        self.agent = JobAgentService(llm=_ProviderLlmClient(self._provider), tools=self.tools)
        return self.agent

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
                logger.debug("[job-agent] napcat renderer failed", exc_info=True)
        return TextCardRenderer().render(card, token)

    def _build_adapter(self) -> Any:
        app_id = str(self._cfg("app_id", "") or "").strip()
        app_secret = str(self._cfg("app_secret", "") or "").strip()
        app_token = str(self._cfg("app_token", "") or "").strip()
        table_id = str(self._cfg("table_id", "") or "").strip()
        table_url = str(self._cfg("feishu_table_url", "") or "").strip()
        if not app_token or not table_id:
            url_app_token, url_table_id = parse_table_url(table_url)
            app_token = app_token or (url_app_token or "")
            table_id = table_id or (url_table_id or "")
        missing = [
            name
            for name, value in (
                ("app_id", app_id),
                ("app_secret", app_secret),
                ("app_token/feishu_table_url", app_token),
                ("table_id/feishu_table_url", table_id),
            )
            if not value
        ]
        if missing:
            self._adapter_reason = "飞书 API 配置不完整，缺少：" + "、".join(missing)
            logger.warning("[job-agent] %s", self._adapter_reason)
            return _MissingFeishuAdapter(self._adapter_reason)
        return FeishuApiAdapter(app_id, app_secret, app_token, table_id)

    async def _resolve_provider(self):
        provider_id = str(self._cfg("provider_id", "") or "").strip()
        if provider_id:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if provider:
                    return provider
                logger.warning("[job-agent] provider_id=%s 未找到，回退到默认 Provider", provider_id)
            except Exception:
                logger.warning("[job-agent] configured provider unavailable", exc_info=True)
        getter = getattr(self.context, "get_using_provider_async", None)
        if getter is not None:
            try:
                provider = await getter()
                if provider is not None:
                    return provider
            except Exception:
                logger.warning("[job-agent] get_using_provider_async failed", exc_info=True)
        try:
            return self.context.get_using_provider()
        except Exception:
            logger.warning("[job-agent] get_using_provider failed", exc_info=True)
            return None

    def _resolve_data_dir(self) -> Path:
        if StarTools is not None:
            try:
                return StarTools.get_data_dir(PLUGIN_NAME)
            except Exception as exc:  # pragma: no cover - 依赖运行环境
                logger.warning("[job-agent] StarTools.get_data_dir 失败(%s)，回退到 plugins/plugin_data", exc)
        return Path(__file__).resolve().parents[1] / "plugin_data" / PLUGIN_NAME

    def _migrate_legacy_state(self) -> None:
        """把旧版写在 plugins/plugin_data 下的状态文件搬过来（只搬一次）。"""
        legacy = Path(__file__).resolve().parents[1] / "plugin_data" / PLUGIN_NAME
        if legacy == self._data_dir or not legacy.exists():
            return
        source = legacy / "job_agent_state.json"
        target = self._data_dir / "job_agent_state.json"
        if source.exists() and not target.exists():
            try:
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                logger.info("[job-agent] 已迁移旧状态文件 %s", source)
            except OSError as exc:
                logger.warning("[job-agent] 迁移旧状态文件失败: %s", exc)

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
