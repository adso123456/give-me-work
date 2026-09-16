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


def test_state_store_card_status_gate_and_pending_count(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_card({"card_id": "c1", "token": "t1"})
        assert await store.is_pending_card("t1") is True
        assert await store.pending_card_count() == 1

        await store.complete_card("t1")
        assert await store.is_pending_card("t1") is False
        assert await store.pending_card_count() == 0
        assert await store.is_pending_card("missing") is False

    asyncio.run(scenario())


def test_processed_event_ids_are_capped(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json", max_processed_events=3)
        await store.load()
        for index in range(5):
            await store.mark_event_processed(f"evt_{index}")

        snapshot = await store.snapshot()
        assert snapshot["processed_event_ids"] == ["evt_2", "evt_3", "evt_4"]
        assert not await store.is_event_processed("evt_0")
        assert await store.is_event_processed("evt_4")

    asyncio.run(scenario())


def test_prune_cards_removes_only_finished_and_expired(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_card({"card_id": "c_old", "token": "t_old", "created_at": 1000.0})
        await store.add_pending_card({"card_id": "c_new", "token": "t_new"})
        await store.complete_card("t_old")

        removed = await store.prune_cards(ttl_seconds=10)
        assert removed == 1
        assert await store.get_pending_card_by_token("t_old") is None
        assert await store.get_pending_card_by_token("t_new") is not None

    asyncio.run(scenario())


def test_latest_pending_card_prefers_newest_for_umo(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_card(
            {"card_id": "c1", "token": "t1", "umo": "umo_a", "created_at": 10.0, "actions": ["replied"]}
        )
        await store.add_pending_card(
            {"card_id": "c2", "token": "t2", "umo": "umo_a", "created_at": 20.0, "actions": ["later"]}
        )
        await store.add_pending_card(
            {"card_id": "c3", "token": "t3", "umo": "umo_b", "created_at": 30.0}
        )

        newest = await store.latest_pending_card("umo_a")
        assert newest["token"] == "t2"
        assert newest["actions"] == ["later"]

        await store.complete_card("t2")
        assert (await store.latest_pending_card("umo_a"))["token"] == "t1"
        assert await store.latest_pending_card("umo_missing") is None

    asyncio.run(scenario())


def test_prune_cards_drops_stale_pending_cards(tmp_path):
    """没人理的待处理卡片也要能过期，否则只增不减。"""
    import time

    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_card(
            {"card_id": "c_old", "token": "t_old", "created_at": time.time() - 7200}
        )
        await store.add_pending_card({"card_id": "c_new", "token": "t_new"})

        removed = await store.prune_cards(ttl_seconds=3600)
        assert removed == 1
        assert await store.get_pending_card_by_token("t_old") is None
        assert await store.get_pending_card_by_token("t_new") is not None

    asyncio.run(scenario())


def test_prune_cards_enforces_max_cards_and_cleans_tokens(tmp_path):
    import time

    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        base = time.time() - 10
        for index in range(4):
            await store.add_pending_card(
                {"card_id": f"c{index}", "token": f"t{index}", "created_at": base + index}
            )

        removed = await store.prune_cards(ttl_seconds=10**6, max_cards=2)
        assert removed == 2
        snapshot = await store.snapshot()
        assert [card["token"] for card in snapshot["pending_cards"]] == ["t2", "t3"]
        assert set(snapshot["card_action_tokens"]) == {"t2", "t3"}

    asyncio.run(scenario())


def test_pending_delete_lifecycle(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_delete(
            {"token": "d1", "record_ids": ["rec1", "rec2"], "umo": "umo_a"}
        )
        item = await store.get_pending_delete("d1")
        assert item["record_ids"] == ["rec1", "rec2"]
        assert item["status"] == "pending"
        assert item["umo"] == "umo_a"

        await store.complete_delete("d1")
        assert (await store.get_pending_delete("d1"))["status"] == "done"
        assert await store.get_pending_delete("missing") is None

    asyncio.run(scenario())


def test_pending_delete_requires_token_and_record_ids(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        with pytest.raises(ValueError):
            await store.add_pending_delete({"record_ids": ["rec1"]})
        with pytest.raises(ValueError):
            await store.add_pending_delete({"token": "d1", "record_ids": []})

    asyncio.run(scenario())


def test_prune_deletes_drops_expired_and_finished(tmp_path):
    from state_store import StateStore

    async def scenario():
        store = StateStore(tmp_path / "state.json")
        await store.load()
        await store.add_pending_delete(
            {"token": "old", "record_ids": ["rec_old"], "created_at": 1000.0}
        )
        await store.add_pending_delete({"token": "new", "record_ids": ["rec_new"]})
        await store.complete_delete("new")

        removed = await store.prune_deletes(ttl_seconds=10)
        assert removed == 2
        assert await store.get_pending_delete("old") is None
        assert await store.get_pending_delete("new") is None

    asyncio.run(scenario())
