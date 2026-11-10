"""Tracer — minimal span-based tracing.

A span is a unit of work with a start/end time, attributes, and events.
Spans are organised under a root trace (same trace_id).

Mirrors ``client/src/observability/tracer.js``.

Usage::

    from server.shared.observability.tracer import Tracer, get_tracer

    tracer = get_tracer()
    with tracer.span("stt.transcribe", {"model": "tiny"}) as span:
        # ... do work ...
        span.event("model.loaded", {"load_ms": 240})
    span.finish("ok")
"""
from __future__ import annotations

import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .context import (
    get_current,
    new_span_id,
    new_trace_id,
    trace_context,
    trace_ctx_var,
)


@dataclass
class SpanEvent:
    name: str
    ts_ms: float
    attributes: dict = field(default_factory=dict)


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    start_ms: float
    end_ms: float = 0.0
    attributes: dict = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    status: str = "ok"  # ok | error | unset

    def event(self, name: str, attributes: Optional[dict] = None) -> None:
        self.events.append(SpanEvent(
            name=name,
            ts_ms=time.time() * 1000,
            attributes=attributes or {},
        ))

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str) -> None:
        self.status = status

    def duration_ms(self) -> float:
        return max(0.0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_ms": self.duration_ms(),
            "status": self.status,
            "attributes": dict(self.attributes),
            "events": [
                {"name": e.name, "ts_ms": e.ts_ms, "attributes": dict(e.attributes)}
                for e in self.events
            ],
        }


class SpanCollector:
    """Receives spans and stores/forwards them. Default = in-memory ring."""

    def __init__(self, max_size: int = 1000) -> None:
        self._lock = threading.Lock()
        self._spans: list[Span] = []
        self._max_size = max_size

    def on_finish(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > self._max_size:
                self._spans = self._spans[-self._max_size:]

    def recent(self, n: int = 50) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._spans[-n:]]

    def by_trace(self, trace_id: str) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._spans if s.trace_id == trace_id]


class Tracer:
    def __init__(self, collector: Optional[SpanCollector] = None) -> None:
        self.collector = collector or SpanCollector()

    @contextmanager
    def span(self, name: str, attributes: Optional[dict] = None) -> Iterator[Span]:
        """Open a span. Uses current trace context if present, else creates a new trace."""
        current = get_current()
        if current["trace_id"]:
            trace_id = current["trace_id"]
            parent = current["span_id"]
            span_id = new_span_id()
        else:
            trace_id = new_trace_id()
            parent = ""
            span_id = new_span_id()

        sp = Span(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent,
            name=name,
            start_ms=time.time() * 1000,
            attributes=dict(attributes or {}),
        )

        token = trace_ctx_var.set({
            "trace_id": trace_id,
            "span_id": span_id,
            "parent_span_id": parent,
        })
        try:
            yield sp
        except Exception as exc:
            sp.set_status("error")
            sp.set_attribute("error.type", type(exc).__name__)
            sp.set_attribute("error.message", str(exc))
            raise
        else:
            if sp.status == "ok":
                sp.set_status("ok")
        finally:
            sp.end_ms = time.time() * 1000
            trace_ctx_var.reset(token)
            self.collector.on_finish(sp)

    def get_collector(self) -> SpanCollector:
        return self.collector


_tracer_lock = threading.Lock()
_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    global _tracer
    with _tracer_lock:
        if _tracer is None:
            _tracer = Tracer()
        return _tracer


def set_tracer(tracer: Tracer) -> None:
    global _tracer
    with _tracer_lock:
        _tracer = tracer
