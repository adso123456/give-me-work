import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer


def _event_payload(event_id="evt_http_1"):
    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "platform": "boss",
        "event_type": "hr_message",
        "conversation_id": "boss_1",
        "company": "__JOB_AGENT_V1_TEST__",
        "position": "测试AI岗位",
        "content": "方便介绍一下你的 Agent 项目吗？",
        "occurred_at": "2026-09-15T12:00:00+08:00",
    }


def test_webhook_auth_dedup_and_async_acceptance(tmp_path):
    try:
        from events.server import EventServer
        from state_store import StateStore
    except ModuleNotFoundError:
        pytest.fail("EventServer API is not implemented yet")

    async def scenario():
        received = []

        async def handler(event):
            await asyncio.sleep(0)
            received.append(event.event_id)

        state = StateStore(tmp_path / "state.json")
        await state.load()
        service = EventServer("secret", state, handler)
        async with TestClient(TestServer(service.app)) as client:
            unauthorized = await client.post("/job-agent/events", json=_event_payload())
            assert unauthorized.status == 401

            accepted = await client.post(
                "/job-agent/events",
                json=_event_payload(),
                headers={"Authorization": "Bearer secret"},
            )
            assert accepted.status == 202
            assert (await accepted.json())["status"] == "accepted"

            # 处理完成后重复投递 → duplicate
            await asyncio.sleep(0.05)
            duplicate = await client.post(
                "/job-agent/events",
                json=_event_payload(),
                headers={"Authorization": "Bearer secret"},
            )
            assert duplicate.status == 200
            assert (await duplicate.json())["status"] == "duplicate"

        assert received == ["evt_http_1"]

    asyncio.run(scenario())


def test_webhook_keeps_event_retryable_when_handler_fails(tmp_path):
    """处理失败的事件不能被标记为已处理，发送方可安全重试。"""
    from events.server import EventServer
    from state_store import StateStore

    async def scenario():
        async def failing_handler(event):
            raise RuntimeError("boom")

        state = StateStore(tmp_path / "state.json")
        await state.load()
        service = EventServer("secret", state, failing_handler)
        async with TestClient(TestServer(service.app)) as client:
            first = await client.post(
                "/job-agent/events",
                json=_event_payload("evt_fail_1"),
                headers={"Authorization": "Bearer secret"},
            )
            assert first.status == 202
            await asyncio.sleep(0.05)

            assert not await state.is_event_processed("evt_fail_1")
            retry = await client.post(
                "/job-agent/events",
                json=_event_payload("evt_fail_1"),
                headers={"Authorization": "Bearer secret"},
            )
            assert retry.status == 202
            assert (await retry.json())["status"] == "accepted"

    asyncio.run(scenario())


def test_webhook_reports_inflight_event_as_not_yet_processed(tmp_path):
    from events.server import EventServer
    from state_store import StateStore

    async def scenario():
        release = asyncio.Event()

        async def slow_handler(event):
            await release.wait()

        state = StateStore(tmp_path / "state.json")
        await state.load()
        service = EventServer("secret", state, slow_handler)
        async with TestClient(TestServer(service.app)) as client:
            first = await client.post(
                "/job-agent/events",
                json=_event_payload("evt_slow_1"),
                headers={"Authorization": "Bearer secret"},
            )
            assert first.status == 202

            second = await client.post(
                "/job-agent/events",
                json=_event_payload("evt_slow_1"),
                headers={"Authorization": "Bearer secret"},
            )
            assert second.status == 202
            assert (await second.json())["status"] == "in_progress"

            release.set()
            await asyncio.sleep(0.05)
            assert await state.is_event_processed("evt_slow_1")

    asyncio.run(scenario())


def test_mock_event_source_produces_supported_events():
    try:
        from events.mock import MockEventSource
    except ModuleNotFoundError:
        pytest.fail("MockEventSource API is not implemented yet")

    event = MockEventSource.hr_message(event_id="evt_mock_1")
    assert event.platform == "boss"
    assert event.event_type == "hr_message"
