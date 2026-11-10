"""SqlServerStrategy — Microsoft SQL Server-backed event repository.

Requires ``pyodbc`` or ``aioodbc`` (optional dep). Same column names
as SqliteStrategy / PostgresStrategy for portability.
"""
from __future__ import annotations

import json
from typing import Optional

from ...repository import EventRepository


SCHEMA = """
IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='events' AND xtype='U')
CREATE TABLE events (
    seq BIGINT IDENTITY(1,1) PRIMARY KEY,
    id NVARCHAR(64) UNIQUE NOT NULL,
    type NVARCHAR(128) NOT NULL,
    source NVARCHAR(128) NOT NULL,
    ts DATETIME2 NOT NULL,
    ts_ms FLOAT NOT NULL,
    correlation_id NVARCHAR(128) NULL,
    causation_id NVARCHAR(64) NULL,
    trace_id NVARCHAR(64) NULL,
    span_id NVARCHAR(32) NULL,
    payload NVARCHAR(MAX) NOT NULL,
    attributes NVARCHAR(MAX) NULL
);
"""


class SqlServerStrategy(EventRepository):
    """Uses aioodbc. Same column names as the other strategies."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        try:
            import aioodbc
        except ImportError as e:
            raise RuntimeError(
                "SqlServerStrategy requires 'aioodbc' + pyodbc driver. "
                "Install with: pip install aioodbc pyodbc"
            ) from e
        self._aioodbc = aioodbc
        self._pool = await aioodbc.create_pool(self._dsn, minsize=1, maxsize=8)
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(SCHEMA)
            await conn.commit()

    async def _conn(self):
        if self._pool is None:
            await self.connect()
        return await self._pool.acquire()

    async def append(self, event: dict) -> dict:
        conn = await self._conn()
        try:
            trace = event.get("trace") or {}
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO events (id, type, source, ts, ts_ms, correlation_id,
                                        causation_id, trace_id, span_id, payload, attributes)
                    OUTPUT INSERTED.seq
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["id"], event["type"], event["source"], event["ts"], event["ts_ms"],
                        event.get("correlation_id"), event.get("causation_id"),
                        trace.get("trace_id"), trace.get("span_id"),
                        json.dumps(event.get("payload", {})),
                        json.dumps(event.get("attributes", {})),
                    ),
                )
                row = await cur.fetchone()
                seq = int(row[0]) if row else None
            await conn.commit()
            return {**event, "seq": seq}
        finally:
            await self._pool.release(conn)

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM events WHERE id = ?", (event_id,))
                row = await cur.fetchone()
            return self._row_to_event(row) if row else None
        finally:
            await self._pool.release(conn)

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE correlation_id = ? ORDER BY seq ASC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (correlation_id, limit),
                )
                rows = await cur.fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE trace_id = ? ORDER BY seq ASC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (trace_id, limit),
                )
                rows = await cur.fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE type = ? ORDER BY seq DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (event_type, limit),
                )
                rows = await cur.fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY",
                    (seq, limit),
                )
                rows = await cur.fetchall()
            return [self._row_to_event(r) for r in rows]
        finally:
            await self._pool.release(conn)

    async def latest_seq(self) -> int:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COALESCE(MAX(seq), 0) FROM events")
                row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await self._pool.release(conn)

    async def count(self) -> int:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM events")
                row = await cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            await self._pool.release(conn)

    async def clear(self) -> None:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM events")
            await conn.commit()
        finally:
            await self._pool.release(conn)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()

    @staticmethod
    def _row_to_event(row) -> dict:
        if not row:
            return None
        payload = row[10]
        if isinstance(payload, str):
            payload = json.loads(payload)
        attributes = row[11]
        if isinstance(attributes, str):
            attributes = json.loads(attributes) if attributes else {}
        return {
            "seq": row[0],
            "id": row[1],
            "type": row[2],
            "source": row[3],
            "ts": row[4].isoformat() if hasattr(row[4], "isoformat") else row[4],
            "ts_ms": row[5],
            "correlation_id": row[6],
            "causation_id": row[7],
            "trace_id": row[8],
            "span_id": row[9],
            "payload": payload,
            "attributes": attributes,
        }
