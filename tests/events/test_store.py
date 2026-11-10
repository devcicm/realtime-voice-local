"""Tests for the EventStore + strategies (memory, sqlite, jsonl).

Postgres and SQLServer are tested directly via in-memory connection
where possible, otherwise the import-path and constructor are validated.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from server.shared.events import (
    EventStore,
    EventType,
    Event,
    MemoryStrategy,
    SqliteStrategy,
    JsonlStrategy,
    create_store,
)


def _make_event(type_, **kwargs):
    """Build a minimal event dict for storage."""
    e = Event(type=type_, source="test", payload=kwargs.pop("payload", {}))
    d = e.to_dict()
    d.update(kwargs)
    return d


# ---- MemoryStrategy ----

@pytest.mark.asyncio
async def test_memory_append_assigns_seq():
    store = EventStore.memory()
    e1 = await store.append(_make_event(EventType.LLM_TOKEN_DELTA, payload={"text": "a"}))
    e2 = await store.append(_make_event(EventType.LLM_TOKEN_DELTA, payload={"text": "b"}))
    assert e1["seq"] == 1
    assert e2["seq"] == 2


@pytest.mark.asyncio
async def test_memory_get_by_correlation_id():
    store = EventStore.memory()
    await store.append(_make_event(EventType.CONN_OPEN, correlation_id="trace-1"))
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA, correlation_id="trace-1"))
    await store.append(_make_event(EventType.CONN_OPEN, correlation_id="trace-2"))
    events = await store.get_by_correlation_id("trace-1")
    assert len(events) == 2
    assert all(e["correlation_id"] == "trace-1" for e in events)


@pytest.mark.asyncio
async def test_memory_get_by_trace_id():
    store = EventStore.memory()
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA, trace={"trace_id": "t1", "span_id": "s1"}))
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA, trace={"trace_id": "t2", "span_id": "s2"}))
    events = await store.get_by_trace_id("t1")
    assert len(events) == 1
    assert events[0]["trace"]["trace_id"] == "t1"


@pytest.mark.asyncio
async def test_memory_get_by_type():
    store = EventStore.memory()
    for _ in range(3):
        await store.append(_make_event(EventType.LLM_TOKEN_DELTA))
    await store.append(_make_event(EventType.TTS_AUDIO_DELTA))
    events = await store.get_by_type(EventType.LLM_TOKEN_DELTA)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_memory_get_since_excludes_passed_seq():
    store = EventStore.memory()
    for i in range(5):
        await store.append(_make_event(EventType.CONN_OPEN))
    events = await store.get_since(seq=2)
    assert all(e["seq"] > 2 for e in events)
    assert len(events) == 3


@pytest.mark.asyncio
async def test_memory_latest_seq():
    store = EventStore.memory()
    assert await store.latest_seq() == 0
    for _ in range(4):
        await store.append(_make_event(EventType.CONN_OPEN))
    assert await store.latest_seq() == 4


@pytest.mark.asyncio
async def test_memory_fold_reducer():
    store = EventStore.memory()
    reducers = {
        EventType.CONN_OPEN:    lambda s, e: {**s, "open": s["open"] + 1},
        EventType.LLM_TOKEN_DELTA: lambda s, e: {**s, "tokens": s["tokens"] + 1},
    }
    await store.append(_make_event(EventType.CONN_OPEN))
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA))
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA))
    await store.append(_make_event(EventType.LLM_TOKEN_DELTA))
    final = await store.fold({"open": 0, "tokens": 0}, reducers)
    assert final == {"open": 1, "tokens": 3}


@pytest.mark.asyncio
async def test_memory_clear():
    store = EventStore.memory()
    for _ in range(5):
        await store.append(_make_event(EventType.CONN_OPEN))
    await store.clear()
    assert await store.count() == 0
    assert await store.latest_seq() == 0


@pytest.mark.asyncio
async def test_memory_subscribe():
    store = EventStore.memory()
    seen = []
    unsub = store.subscribe(lambda e: seen.append(e))
    await store.append(_make_event(EventType.CONN_OPEN))
    await store.append(_make_event(EventType.CONN_OPEN))
    unsub()
    await store.append(_make_event(EventType.CONN_OPEN))
    assert len(seen) == 2


# ---- SqliteStrategy ----

@pytest.mark.asyncio
async def test_sqlite_in_memory_roundtrip():
    repo = SqliteStrategy(":memory:", in_memory=True)
    e1 = await repo.append(_make_event(EventType.LLM_TOKEN_DELTA, payload={"text": "a"}))
    e2 = await repo.append(_make_event(EventType.LLM_TOKEN_DELTA, payload={"text": "b"}))
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    fetched = await repo.get_by_id(e1["id"])
    assert fetched["payload"] == {"text": "a"}


@pytest.mark.asyncio
async def test_sqlite_persists_to_file(tmp_path):
    db_path = tmp_path / "events.db"
    repo1 = SqliteStrategy(db_path)
    e1 = await repo1.append(_make_event(EventType.CONN_OPEN, correlation_id="x"))
    await repo1.close()
    # Reopen — must be persisted.
    repo2 = SqliteStrategy(db_path)
    fetched = await repo2.get_by_id(e1["id"])
    assert fetched is not None
    assert fetched["correlation_id"] == "x"
    await repo2.close()


@pytest.mark.asyncio
async def test_sqlite_correlation_id_query():
    repo = SqliteStrategy(":memory:", in_memory=True)
    for _ in range(3):
        await repo.append(_make_event(EventType.CONN_OPEN, correlation_id="c1"))
    for _ in range(2):
        await repo.append(_make_event(EventType.CONN_OPEN, correlation_id="c2"))
    events = await repo.get_by_correlation_id("c1")
    assert len(events) == 3


@pytest.mark.asyncio
async def test_sqlite_count_and_clear():
    repo = SqliteStrategy(":memory:", in_memory=True)
    for _ in range(7):
        await repo.append(_make_event(EventType.CONN_OPEN))
    assert await repo.count() == 7
    await repo.clear()
    assert await repo.count() == 0


@pytest.mark.asyncio
async def test_sqlite_eventstore_factory(tmp_path):
    """EventStore.sqlite() factory creates a working store."""
    store = EventStore.sqlite(tmp_path / "events.db")
    await store.append(_make_event(EventType.CONN_OPEN, correlation_id="c1"))
    events = await store.get_by_correlation_id("c1")
    assert len(events) == 1
    await store.close()


# ---- JsonlStrategy ----

@pytest.mark.asyncio
async def test_jsonl_writes_to_file(tmp_path):
    path = tmp_path / "events.jsonl"
    repo = JsonlStrategy(path)
    e = await repo.append(_make_event(EventType.CONN_OPEN, payload={"k": "v"}))
    await repo.close()
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["type"] == EventType.CONN_OPEN
    assert parsed["payload"] == {"k": "v"}


@pytest.mark.asyncio
async def test_jsonl_latest_seq_reflects_appends(tmp_path):
    path = tmp_path / "events.jsonl"
    repo = JsonlStrategy(path)
    for _ in range(3):
        await repo.append(_make_event(EventType.CONN_OPEN))
    assert await repo.latest_seq() == 3
    await repo.close()


@pytest.mark.asyncio
async def test_jsonl_clear_removes_file(tmp_path):
    path = tmp_path / "events.jsonl"
    repo = JsonlStrategy(path)
    await repo.append(_make_event(EventType.CONN_OPEN))
    await repo.clear()
    assert not path.exists()


# ---- Factory ----

def test_create_store_memory():
    store = create_store("memory", max_size=10)
    assert isinstance(store, EventStore)


def test_create_store_sqlite(tmp_path):
    store = create_store("sqlite", db_path=tmp_path / "events.db")
    assert isinstance(store, EventStore)


def test_create_store_jsonl(tmp_path):
    store = create_store("jsonl", path=tmp_path / "events.jsonl")
    assert isinstance(store, EventStore)


def test_create_store_unknown_raises():
    with pytest.raises(ValueError, match="Unknown"):
        create_store("not-a-backend")


# ---- Optional strategies (constructor + import) ----

def test_postgres_strategy_imports_without_asyncpg():
    """PostgresStrategy constructor raises clear error if asyncpg is missing."""
    from server.shared.events.store.strategies.postgres import PostgresStrategy
    try:
        s = PostgresStrategy("postgresql://invalid:test@localhost:1/x")
        # If asyncpg IS installed, the constructor succeeds but connect() fails.
        # Either way, the class is importable.
        assert s is not None
    except RuntimeError as e:
        assert "asyncpg" in str(e)


def test_sqlserver_strategy_imports():
    from server.shared.events.store.strategies.sqlserver import SqlServerStrategy
    try:
        s = SqlServerStrategy("invalid")
        assert s is not None
    except RuntimeError as e:
        assert "aioodbc" in str(e) or "pyodbc" in str(e)
