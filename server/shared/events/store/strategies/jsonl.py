"""JsonlStrategy — newline-delimited JSON append-only file.

Best for backup/export, not for queries. Each line is a complete event.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ...repository import EventRepository


class JsonlStrategy(EventRepository):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        if self._path.exists():
            # Count existing lines to recover seq number.
            with self._path.open("r", encoding="utf-8") as f:
                self._seq = sum(1 for _ in f)
        self._cache: dict[str, dict] = {}
        self._lock = __import__("threading").Lock()

    async def append(self, event: dict) -> dict:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._append_sync, event)
        return event

    def _append_sync(self, event: dict) -> None:
        with self._lock:
            self._seq += 1
            event = {**event, "seq": self._seq}
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
            self._cache[event["id"]] = event

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        if event_id in self._cache:
            return self._cache[event_id]
        return None

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._cache.values() if e.get("correlation_id") == correlation_id][:limit]

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._cache.values() if (e.get("trace") or {}).get("trace_id") == trace_id][:limit]

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        return [e for e in self._cache.values() if e.get("type") == event_type][:limit]

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        return [e for e in self._cache.values() if e["seq"] > seq][:limit]

    async def latest_seq(self) -> int:
        return self._seq

    async def count(self) -> int:
        return self._seq

    async def clear(self) -> None:
        with self._lock:
            if self._path.exists():
                self._path.unlink()
            self._cache.clear()
            self._seq = 0

    async def close(self) -> None:
        pass
