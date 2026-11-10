"""Tests for crash recovery: replay + snapshot + fold."""
import pytest

from server.shared.events import (
    EventStore, EventType, Event,
    replay_from, get_snapshot_at,
)


def _ev(type_, **kw):
    e = Event(type=type_, source="test", payload=kw.pop("payload", {}))
    d = e.to_dict()
    d.update(kw)
    return d


@pytest.mark.asyncio
async def test_replay_from_returns_events_after_seq():
    store = EventStore.memory()
    for i in range(5):
        await store.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"i": i}))
    # Read events after seq 3 (should be 2: seq 4 and 5).
    events = await store.get_since(seq=3, limit=10)
    assert len(events) == 2
    # Replay should reduce to a state object.
    final = await replay_from(store, since_seq=3, reducers={}, initial_state={})
    assert final == {}  # no reducers, no changes


@pytest.mark.asyncio
async def test_replay_from_with_reducer_rebuilds_state():
    store = EventStore.memory()
    await store.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"text": "a"}))
    await store.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"text": "b"}))
    await store.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"text": "c"}))

    reducers = {
        EventType.LLM_TOKEN_DELTA: lambda s, e: {**s, "tokens": s.get("tokens", 0) + 1},
    }
    state = await replay_from(store, since_seq=0, reducers=reducers, initial_state={"tokens": 0})
    assert state == {"tokens": 3}


@pytest.mark.asyncio
async def test_crash_recovery_simulation():
    """Simulate a full crash-recovery cycle.

    1. Run a session, capturing events.
    2. Capture the seq at some point.
    3. "Crash" — discard the in-memory state.
    4. "Restart" — read everything from the store, replay from snapshot seq.
    """
    store = EventStore.memory()
    for i in range(10):
        await store.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"i": i}))

    # Snapshot at seq 5
    snapshot_seq = 5

    # Crash + restart: read state from scratch
    reducers = {
        EventType.LLM_TOKEN_DELTA: lambda s, e: {**s, "count": s.get("count", 0) + 1},
    }
    # If we had no snapshot, replay all events.
    state_from_scratch = await replay_from(store, since_seq=0, reducers=reducers, initial_state={})
    assert state_from_scratch["count"] == 10

    # If we had a snapshot at seq 5 with count=5, replay from there.
    state_from_snapshot = await replay_from(
        store, since_seq=snapshot_seq,
        reducers=reducers,
        initial_state={"count": 5},
    )
    assert state_from_snapshot["count"] == 10  # 5 from snapshot + 5 new


@pytest.mark.asyncio
async def test_get_snapshot_at_returns_events_up_to_seq():
    store = EventStore.memory()
    for i in range(5):
        await store.append(_ev(EventType.CONN_OPEN, payload={"i": i}))
    snap = await get_snapshot_at(store, seq=3)
    assert len(snap) == 3
    assert all(e["seq"] <= 3 for e in snap)


@pytest.mark.asyncio
async def test_persist_replay_across_sqlite_close_open(tmp_path):
    """Recovery works across a process restart with SQLite."""
    db_path = tmp_path / "recovery.db"
    store1 = EventStore.sqlite(db_path)
    for i in range(3):
        await store1.append(_ev(EventType.LLM_TOKEN_DELTA, payload={"i": i}))
    await store1.close()

    # "Restart" — open the same DB.
    store2 = EventStore.sqlite(db_path)
    reducers = {
        EventType.LLM_TOKEN_DELTA: lambda s, e: {**s, "count": s.get("count", 0) + 1},
    }
    state = await replay_from(store2, since_seq=0, reducers=reducers, initial_state={})
    assert state["count"] == 3
    await store2.close()
