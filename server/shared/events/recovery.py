"""Crash recovery — given a sequence number, replay missed events.

Used by the client (or another service) to recover state after a
restart. The caller passes the last seq they saw, and the store
returns all events with seq > that.

Combined with the snapshot pattern, this gives full crash recovery:

  1. On startup, read the latest snapshot (state at seq N).
  2. Call replay(since_seq=N) to get events after the snapshot.
  3. Apply per-type reducers to fold the events into current state.
"""
from __future__ import annotations

from typing import Callable

from .store import EventStore


async def replay_from(
    store: EventStore,
    since_seq: int,
    reducers: dict[str, Callable[[dict, dict], dict]],
    initial_state: dict,
) -> dict:
    """Apply reducers to all events with seq > since_seq.

    Returns the final state.
    """
    return await store.fold(initial_state, reducers, since_seq=since_seq)


async def get_snapshot_at(store: EventStore, seq: int) -> dict:
    """Return events up to and including seq, useful for snapshots."""
    if seq <= 0:
        return []
    events = await store.get_since(0, limit=seq)
    return events
