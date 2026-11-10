"""PostgresStrategy — PostgreSQL-backed event repository.

Requires ``asyncpg`` (optional dep). Schema is compatible with the
sqlite strategies (column names match).
"""
from __future__ import annotations

import json
from typing import Optional

from ...repository import EventRepository


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    ts_ms DOUBLE PRECISION NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    trace_id TEXT,
    span_id TEXT,
    payload JSONB NOT NULL,
    attributes JSONB
);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events (correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events (trace_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (type);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts_ms);
"""


class PostgresStrategy(EventRepository):
    """Uses asyncpg. Drop-in for SqliteStrategy — same column names."""

    def __init__(self, dsn: str) -> None:
        try:
            import asyncpg
        except ImportError as e:
            raise RuntimeError(
                "PostgresStrategy requires 'asyncpg'. Install with: pip install asyncpg"
            ) from e
        self._asyncpg = asyncpg
        self._pool: Optional["asyncpg.Pool"] = None
        self._dsn = dsn

    async def connect(self) -> None:
        self._pool = await self._asyncpg.create_pool(self._dsn, min_size=1, max_size=8)
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA)

    async def _conn(self):
        if self._pool is None:
            await self.connect()
        return await self._pool.acquire()

    async def append(self, event: dict) -> dict:
        conn = await self._conn()
        try:
            trace = event.get("trace") or {}
            row = await conn.fetchrow(
                """
                INSERT INTO events (id, type, source, ts, ts_ms, correlation_id, causation_id,
                                    trace_id, span_id, payload, attributes)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING seq
                """,
                event["id"],
                event["type"],
                event["source"],
                event["ts"],
                event["ts_ms"],
                event.get("correlation_id"),
                event.get("causation_id"),
                trace.get("trace_id"),
                trace.get("span_id"),
                json.dumps(event.get("payload", {})),
                json.dumps(event.get("attributes", {})),
            )
            return {**event, "seq": row["seq"]}
        finally:
            await self._pool.release(conn)

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        conn = await self._conn()
        try:
            row = await conn.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
            return self._row_to_event(row) if row else None
        finally:
            await self._pool.release(conn)

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                "SELECT * FROM events WHERE correlation_id = $1 ORDER BY seq ASC LIMIT $2",
                correlation_id, limit,
            )
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                "SELECT * FROM events WHERE trace_id = $1 ORDER BY seq ASC LIMIT $2",
                trace_id, limit,
            )
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                "SELECT * FROM events WHERE type = $1 ORDER BY seq DESC LIMIT $2",
                event_type, limit,
            )
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            rows = await conn.fetch(
                "SELECT * FROM events WHERE seq > $1 ORDER BY seq ASC LIMIT $2",
                seq, limit,
            )
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def latest_seq(self) -> int:
        conn = await self._conn()
        try:
            val = await conn.fetchval("SELECT COALESCE(MAX(seq), 0) FROM events")
            return int(val)
        finally:
            await self._pool.release(conn)

    async def count(self) -> int:
        conn = await self._conn()
        try:
            val = await conn.fetchval("SELECT COUNT(*) FROM events")
            return int(val)
        finally:
            await self._pool.release(conn)

    async def clear(self) -> None:
        conn = await self._conn()
        try:
            await conn.execute("TRUNCATE TABLE events")
        finally:
            await self._pool.release(conn)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @staticmethod
    def _row_to_event(row) -> dict:
        d = dict(row)
        # asyncpg returns JSONB as bytes sometimes; normalise.
        if isinstance(d.get("payload"), (bytes, bytearray)):
            d["payload"] = json.loads(d["payload"])
        if isinstance(d.get("attributes"), (bytes, bytearray)):
            d["attributes"] = json.loads(d["attributes"])
        return d
