"""MemoryStrategy — in-memory event repository.

Simple, fast, ephemeral. Loses data on restart. Used for tests and
the default in-process cache.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from ...repository import EventRepository


class MemoryStrategy(EventRepository):
    def __init__(self, max_size: int = 10000) -> None:
        self._lock = asyncio.Lock()
        self._events: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._seq = 0
        self._max = max_size

    async def append(self, event: dict) -> dict:
        async with self._lock:
            self._seq += 1
            event = {**event, "seq": self._seq}
            self._events.append(event)
            self._by_id[event["id"]] = event
            if len(self._events) > self._max:
                old = self._events[:len(self._events) - self._max]
                self._events = self._events[-self._max:]
                for ev in old:
                    self._by_id.pop(ev["id"], None)
        return event

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        return self._by_id.get(event_id)

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._events if e.get("correlation_id") == correlation_id][:limit]

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._events if (e.get("trace") or {}).get("trace_id") == trace_id][:limit]

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._events if e.get("type") == event_type][:limit]

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        return [e for e in self._events if e["seq"] > seq][:limit]

    async def latest_seq(self) -> int:
        return self._seq

    async def count(self) -> int:
        return len(self._events)

    async def clear(self) -> None:
        async with self._lock:
            self._events.clear()
            self._by_id.clear()
            self._seq = 0

    async def close(self) -> None:
        await self.clear()
