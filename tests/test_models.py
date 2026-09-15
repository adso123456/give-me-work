from datetime import datetime

import pytest


def test_recruit_event_round_trips_and_normalizes_datetime():
    try:
        from models import RecruitEvent
    except ModuleNotFoundError:
        pytest.fail("RecruitEvent API is not implemented yet")

    event = RecruitEvent.from_dict(
        {
            "schema_version": "1.0",
            "event_id": "evt_test_001",
            "platform": "boss",
            "event_type": "hr_message",
            "conversation_id": "boss_test_01",
            "company": "XX科技",
            "position": "AI应用开发工程师",
            "contact": "王女士",
            "content": "方便介绍一下你的 Agent 项目吗？",
            "occurred_at": "2026-09-15T12:00:00+08:00",
        }
    )

    assert event.event_id == "evt_test_001"
    assert isinstance(event.occurred_at, datetime)
    assert event.to_dict()["occurred_at"].startswith("2026-09-15T12:00:00")


def test_card_action_rejects_unknown_action():
    try:
        from models import CardAction
    except ModuleNotFoundError:
        pytest.fail("CardAction API is not implemented yet")

    try:
        CardAction(
            action_id="a1",
            card_id="c1",
            event_id="e1",
            record_id="r1",
            action="send_boss_reply",
        )
    except ValueError as exc:
        assert "action" in str(exc)
    else:
        raise AssertionError("unknown card action must be rejected")
