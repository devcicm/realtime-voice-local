"""Event envelope — the canonical shape for everything that flows
through the EventBus.

Mirrors ``client/src/events/envelope.js``. Required fields:

  - id            uuid, unique per event
  - type          dotted-string from EventType
  - source        which component produced it (e.g. "tts.stage")
  - ts            ISO-8601 timestamp
  - ts_ms         monotonic ms (matches performance.now() / time.time()*1000)
  - payload       arbitrary dict
  - correlation_id optional join key (e.g. trace_id) used by voice↔chat correlation
  - causation_id  optional id of the event that caused this one
  - trace         optional {trace_id, span_id} for cross-process tracing
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Event:
    type: str
    source: str
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=_new_id)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z")
    ts_ms: float = field(default_factory=lambda: time.time() * 1000)
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    trace: Optional[dict] = None
    attributes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def clone(self, **overrides) -> "Event":
        d = self.to_dict()
        d.update(overrides)
        return Event(**d)
