"""插件运行状态的原子 JSON 持久化。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "bound_umo": None,
        "processed_event_ids": [],
        "pending_cards": [],
        "card_action_tokens": {},
    }


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._state = _default_state()

    @property
    def bound_umo(self) -> str | None:
        return self._state.get("bound_umo")

    async def load(self) -> None:
        async with self._lock:
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        state = _default_state()
                        state.update(loaded)
                        self._state = state
                except (OSError, json.JSONDecodeError):
                    self._state = _default_state()
            await self._persist_unlocked()

    async def bind_umo(self, umo: str) -> None:
        if not umo.strip():
            raise ValueError("umo is required")
        async with self._lock:
            self._state["bound_umo"] = umo
            await self._persist_unlocked()

    async def is_event_processed(self, event_id: str) -> bool:
        async with self._lock:
            return event_id in self._state["processed_event_ids"]

    async def mark_event_processed(self, event_id: str) -> None:
        if not event_id.strip():
            raise ValueError("event_id is required")
        async with self._lock:
            ids = self._state["processed_event_ids"]
            if event_id not in ids:
                ids.append(event_id)
                await self._persist_unlocked()

    async def add_pending_card(self, card: dict[str, Any]) -> None:
        token = str(card.get("token") or "").strip()
        if not token:
            raise ValueError("pending card token is required")
        async with self._lock:
            item = dict(card)
            item.setdefault("status", "pending")
            cards = [x for x in self._state["pending_cards"] if x.get("token") != token]
            cards.append(item)
            self._state["pending_cards"] = cards
            self._state["card_action_tokens"][token] = item.get("card_id")
            await self._persist_unlocked()

    async def get_pending_card_by_token(self, token: str) -> dict[str, Any] | None:
        async with self._lock:
            for card in self._state["pending_cards"]:
                if card.get("token") == token:
                    return dict(card)
        return None

    async def complete_card(self, token: str) -> None:
        async with self._lock:
            for card in self._state["pending_cards"]:
                if card.get("token") == token:
                    card["status"] = "completed"
                    break
            await self._persist_unlocked()

    async def pending_card_count(self) -> int:
        async with self._lock:
            return sum(1 for card in self._state["pending_cards"] if card.get("status") == "pending")

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    async def _persist_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f".{self.path.name}.tmp")
        temp_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
