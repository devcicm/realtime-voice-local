"""SqliteStrategy — SQLite-backed event repository.

Stores events in a single table with indexes on correlation_id, trace_id,
type, and seq. SQLite is the default backend for servers (single-process,
durable, no external dependencies).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ...repository import EventRepository


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    source TEXT NOT NULL,
    ts TEXT NOT NULL,
    ts_ms REAL NOT NULL,
    correlation_id TEXT,
    causation_id TEXT,
    trace_id TEXT,
    span_id TEXT,
    payload TEXT NOT NULL,
    attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ms);
"""

# Migrations run in order. Idempotent.
MIGRATIONS = [
    ("0.1.0", SCHEMA),
    ("0.2.0", "CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(type, ts_ms);"),
]


class SqliteStrategy(EventRepository):
    def __init__(self, db_path: str | Path, *, in_memory: bool = False) -> None:
        self._db_path = ":memory:" if in_memory else str(db_path)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        self._conn.execute("CREATE TABLE IF NOT EXISTS _migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);")
        for version, sql in MIGRATIONS:
            cur = self._conn.execute("SELECT 1 FROM _migrations WHERE version = ?", (version,))
            if cur.fetchone() is None:
                self._conn.executescript(sql)
                self._conn.execute("INSERT INTO _migrations (version) VALUES (?)", (version,))

    def _row_to_event(self, row: sqlite3.Row) -> dict:
        return {
            "seq": row["seq"],
            "id": row["id"],
            "type": row["type"],
            "source": row["source"],
            "ts": row["ts"],
            "ts_ms": row["ts_ms"],
            "correlation_id": row["correlation_id"],
            "causation_id": row["causation_id"],
            "trace_id": row["trace_id"],
            "span_id": row["span_id"],
            "payload": json.loads(row["payload"]) if row["payload"] else None,
            "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
        }

    async def append(self, event: dict) -> dict:
        trace = event.get("trace") or {}
        cur = self._conn.execute(
            """
            INSERT INTO events (id, type, source, ts, ts_ms, correlation_id, causation_id,
                                trace_id, span_id, payload, attributes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
            ),
        )
        event = {**event, "seq": cur.lastrowid}
        return event

    async def get_by_id(self, event_id: str) -> Optional[dict]:
        cur = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        row = cur.fetchone()
        return self._row_to_event(row) if row else None

    async def get_by_correlation_id(self, correlation_id: str, limit: int = 1000) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE correlation_id = ? ORDER BY seq ASC LIMIT ?",
            (correlation_id, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    async def get_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE trace_id = ? ORDER BY seq ASC LIMIT ?",
            (trace_id, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    async def get_by_type(self, event_type: str, limit: int = 1000) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE type = ? ORDER BY seq DESC LIMIT ?",
            (event_type, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    async def get_since(self, seq: int, limit: int = 1000) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC LIMIT ?",
            (seq, limit),
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    async def latest_seq(self) -> int:
        cur = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events")
        return cur.fetchone()[0]

    async def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM events")
        return cur.fetchone()[0]

    async def clear(self) -> None:
        self._conn.execute("DELETE FROM events")

    async def close(self) -> None:
        self._conn.close()
