import asyncio
import json

import pytest


def test_state_store_persists_binding_pending_card_and_event_dedup(tmp_path):
    try:
        from state_store import StateStore
    except ModuleNotFoundError:
        pytest.fail("StateStore API is not implemented yet")

    async def scenario():
        path = tmp_path / "job_agent_state.json"
        store = StateStore(path)
        await store.load()
        await store.bind_umo("aiocqhttp:GroupMessage:123")
        await store.mark_event_processed("evt_1")
        await store.add_pending_card(
            {
                "card_id": "card_1",
                "event_id": "evt_1",
                "record_id": "rec_1",
                "token": "tok_1",
            }
        )

        restored = StateStore(path)
        await restored.load()
        assert restored.bound_umo == "aiocqhttp:GroupMessage:123"
        assert await restored.is_event_processed("evt_1")
        assert (await restored.get_pending_card_by_token("tok_1"))["record_id"] == "rec_1"

        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["bound_umo"] == "aiocqhttp:GroupMessage:123"

    asyncio.run(scenario())


def test_state_store_completes_card_without_removing_history(tmp_path):
    try:
        from state_store import StateStore
    except ModuleNotFoundError:
        pytest.fail("StateStore API is not implemented yet")

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_card({"card_id": "c1", "token": "t1", "status": "pending"})
        await store.complete_card("t1")
        card = await store.get_pending_card_by_token("t1")
        assert card["status"] == "completed"

    asyncio.run(scenario())
