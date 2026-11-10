"""EventStore — high-level wrapper around a Strategy.

Combines a repository with helpers for:
  - snapshots (every N events, the current state is captured)
  - replay (reconstruct events from a given seq)
  - reducer-based state fold (event sourcing)
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..repository import EventRepository
from .strategies.memory import MemoryStrategy


@dataclass
class Snapshot:
    seq: int
    state: dict
    taken_at_ms: float


class EventStore:
    def __init__(
        self,
        repository: EventRepository,
        snapshot_interval: int = 100,
    ) -> None:
        self._repo = repository
        self._snapshot_interval = snapshot_interval
        self._last_snapshot_seq: int = 0
        self._subscribers: list[Callable[[dict], None]] = []

    @classmethod
    def memory(cls, max_size: int = 10000) -> "EventStore":
        return cls(MemoryStrategy(max_size=max_size))

    @classmethod
    def sqlite(cls, db_path: str | "Path") -> "EventStore":
        from .strategies.sqlite import SqliteStrategy
        return cls(SqliteStrategy(db_path))

    @classmethod
    def jsonl(cls, path: str | "Path") -> "EventStore":
        from .strategies.jsonl import JsonlStrategy
        return cls(JsonlStrategy(path))

    @classmethod
    def postgres(cls, dsn: str) -> "EventStore":
        from .strategies.postgres import PostgresStrategy
        return cls(PostgresStrategy(dsn))

    @classmethod
    def sqlserver(cls, dsn: str) -> "EventStore":
        from .strategies.sqlserver import SqlServerStrategy
        return cls(SqlServerStrategy(dsn))

    # ---- write ----

    async def append(self, event: dict) -> dict:
        stored = await self._repo.append(event)
        if stored["seq"] % self._snapshot_interval == 0:
            self._last_snapshot_seq = stored["seq"]
        for cb in self._subscribers:
            try:
                cb(stored)
            except Exception:
                pass
        return stored

    # ---- read ----

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        return await self._repo.get_by_id(event_id)

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        return await self._repo.get_by_correlation_id(correlation_id, limit)

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        return await self._repo.get_by_trace_id(trace_id, limit)

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        return await self._repo.get_by_type(event_type, limit)

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        return await self._repo.get_since(seq, limit)

    async def latest_seq(self) -> int:
        return await self._repo.latest_seq()

    async def count(self) -> int:
        return await self._repo.count()

    # ---- event sourcing helpers ----

    async def fold(
        self,
        initial_state: dict,
        reducers: dict[str, Callable[[dict, dict], dict]],
        *,
        since_seq: int = 0,
        limit: int = 10000,
    ) -> dict:
        """Replay events from `since_seq` and apply per-type reducer.

        Returns the final state (the result of the last reducer call).
        """
        events = await self.get_since(since_seq, limit=limit)
        state = initial_state
        for e in events:
            reducer = reducers.get(e["type"])
            if reducer:
                state = reducer(state, e)
        return state

    async def clear(self) -> None:
        await self._repo.clear()
        self._last_snapshot_seq = 0

    async def close(self) -> None:
        await self._repo.close()

    # ---- subscriptions ----

    def subscribe(self, callback: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)


# Factory using env-style config.
def create_store(backend: str, **kwargs) -> EventStore:
    if backend == "memory":
        return EventStore.memory(**kwargs)
    if backend == "sqlite":
        return EventStore.sqlite(kwargs["db_path"])
    if backend == "jsonl":
        return EventStore.jsonl(kwargs["path"])
    if backend == "postgres":
        return EventStore.postgres(kwargs["dsn"])
    if backend == "sqlserver":
        return EventStore.sqlserver(kwargs["dsn"])
    raise ValueError(f"Unknown event store backend: {backend!r}")
