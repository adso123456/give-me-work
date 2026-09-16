"""招聘事件来源抽象。"""

from __future__ import annotations

from typing import Protocol

try:
    from ..models import RecruitEvent
except ImportError:  # 兼容直接运行测试文件
    from models import RecruitEvent


class BossEventSource(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class WebhookEventSource:
    """HTTP 事件来源的描述对象，实际监听由 EventServer 完成。"""

    path = "/job-agent/events"


class MockEventSource:
    """用于插件命令和测试的事件构造器。"""

    @staticmethod
    def hr_message(event_id: str = "evt_mock_hr") -> RecruitEvent:
        return RecruitEvent.from_dict(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "platform": "boss",
                "event_type": "hr_message",
                "conversation_id": "boss_mock_01",
                "company": "__JOB_AGENT_V1_TEST__",
                "position": "测试AI岗位",
                "contact": "测试HR",
                "content": "方便介绍一下你的 Agent 项目吗？",
                "occurred_at": "2026-09-15T12:00:00+08:00",
            }
        )

    @staticmethod
    def chat_message(event_id: str = "evt_mock_chat") -> RecruitEvent:
        """模拟一条 BOSS 聊天流水（只推通知、不落表）。"""
        return RecruitEvent.from_dict(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "platform": "boss",
                "event_type": "chat_message",
                "conversation_id": "boss_mock_chat_01",
                "company": "__JOB_AGENT_V1_TEST__",
                "position": "测试AI岗位",
                "contact": "测试HR",
                "content": "在吗？方便发一份简历看看吗？",
                "occurred_at": "2026-09-15T12:00:00+08:00",
            }
        )

    @staticmethod
    def resume_request(event_id: str = "evt_mock_resume") -> RecruitEvent:
        return RecruitEvent.from_dict(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "platform": "boss",
                "event_type": "resume_request",
                "conversation_id": "boss_mock_01",
                "company": "__JOB_AGENT_V1_TEST__",
                "position": "测试AI岗位",
                "contact": "测试HR",
                "content": "方便发一份简历吗？",
                "occurred_at": "2026-09-15T12:00:00+08:00",
            }
        )
