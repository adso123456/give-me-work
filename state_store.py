"""插件运行状态的原子 JSON 持久化。"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

MAX_PROCESSED_EVENTS = 1000
PENDING_CARD_TTL_SECONDS = 30 * 24 * 3600


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "bound_umo": None,
        "processed_event_ids": [],
        "pending_cards": [],
        "card_action_tokens": {},
    }


class StateStore:
    def __init__(
        self,
        path: str | Path,
        max_processed_events: int = MAX_PROCESSED_EVENTS,
    ):
        self.path = Path(path)
        self.max_processed_events = max(1, int(max_processed_events))
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
        """事件真正处理成功后才调用；列表按上限裁剪，避免无限膨胀。"""
        if not event_id.strip():
            raise ValueError("event_id is required")
        async with self._lock:
            ids = self._state["processed_event_ids"]
            if event_id not in ids:
                ids.append(event_id)
            overflow = len(ids) - self.max_processed_events
            if overflow > 0:
                del ids[:overflow]
            await self._persist_unlocked()

    async def add_pending_card(self, card: dict[str, Any]) -> None:
        token = str(card.get("token") or "").strip()
        if not token:
            raise ValueError("pending card token is required")
        async with self._lock:
            item = dict(card)
            item.setdefault("status", "pending")
            item.setdefault("created_at", time.time())
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

    async def is_pending_card(self, token: str) -> bool:
        card = await self.get_pending_card_by_token(token)
        return bool(card) and str(card.get("status") or "pending") == "pending"

    async def latest_pending_card(self, umo: str | None = None) -> dict[str, Any] | None:
        """最近一张待处理卡片（用于「回复数字」交互）。"""
        async with self._lock:
            candidates = [
                card
                for card in self._state["pending_cards"]
                if str(card.get("status") or "pending") == "pending"
                and (not umo or card.get("umo") == umo)
            ]
            if not candidates:
                return None
            newest = max(candidates, key=lambda card: float(card.get("created_at") or 0))
            return dict(newest)

    async def complete_card(self, token: str) -> None:
        async with self._lock:
            for card in self._state["pending_cards"]:
                if card.get("token") == token:
                    card["status"] = "completed"
                    card.setdefault("completed_at", time.time())
                    break
            await self._persist_unlocked()

    async def pending_card_count(self) -> int:
        async with self._lock:
            return sum(1 for card in self._state["pending_cards"] if card.get("status") == "pending")

    async def prune_cards(self, ttl_seconds: int = PENDING_CARD_TTL_SECONDS) -> int:
        """清掉过期的已完成卡片，返回清理条数。"""
        deadline = time.time() - ttl_seconds
        async with self._lock:
            cards = self._state["pending_cards"]
            kept: list[dict[str, Any]] = []
            removed = 0
            for card in cards:
                created = float(card.get("created_at") or 0)
                finished = str(card.get("status") or "pending") != "pending"
                if finished and created and created < deadline:
                    removed += 1
                    continue
                kept.append(card)
            if removed:
                self._state["pending_cards"] = kept
                alive = {str(card.get("token")) for card in kept}
                self._state["card_action_tokens"] = {
                    token: card_id
                    for token, card_id in (self._state.get("card_action_tokens") or {}).items()
                    if token in alive
                }
                await self._persist_unlocked()
            return removed

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
