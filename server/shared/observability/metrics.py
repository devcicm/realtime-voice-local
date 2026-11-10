"""Metrics — Counter, Gauge, Histogram.

Mirrors ``client/src/observability/metrics.js``. Three primitive types
plus a Registry that holds them and a snapshot() for export.

Usage::

    from server.shared.observability.metrics import get_registry

    reg = get_registry()
    c = reg.counter("stt.transcribe", {"model": "tiny"})
    c.inc()
    c.inc(2)

    h = reg.histogram("tts.first_byte_ms", {"voice": "ef_dora"})
    h.observe(120)

    g = reg.gauge("ws.connections")
    g.set(3)
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable


# Default buckets: 10ms .. 10s. Tuned for voice/TTS latency.
DEFAULT_BUCKETS: tuple[float, ...] = (
    10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000,
)


def _labels_key(labels: dict | None) -> tuple:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


@dataclass
class Counter:
    name: str
    labels: dict
    value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value += amount

    def snapshot(self) -> dict:
        return {"type": "counter", "name": self.name, "labels": self.labels, "value": self.value}


@dataclass
class Gauge:
    name: str
    labels: dict
    value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, v: float) -> None:
        with self._lock:
            self.value = v

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value -= amount

    def snapshot(self) -> dict:
        return {"type": "gauge", "name": self.name, "labels": self.labels, "value": self.value}


@dataclass
class Histogram:
    name: str
    labels: dict
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    counts: list[int] = field(default_factory=list)
    sum_: float = 0.0
    count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)  # +1 for +Inf

    def observe(self, value: float) -> None:
        with self._lock:
            self.sum_ += value
            self.count += 1
            for i, b in enumerate(self.buckets):
                if value <= b:
                    self.counts[i] += 1
                    return
            self.counts[-1] += 1  # +Inf

    def snapshot(self) -> dict:
        # Compute cumulative counts (Prometheus convention).
        with self._lock:
            cumulative = []
            running = 0
            for c in self.counts:
                running += c
                cumulative.append(running)
            # Replace +Inf with a string sentinel for JSON-safety.
            buckets_out = []
            for b in self.buckets:
                if b == float("inf"):
                    buckets_out.append("+Inf")
                else:
                    buckets_out.append(b)
            return {
                "type": "histogram",
                "name": self.name,
                "labels": self.labels,
                "buckets": buckets_out,
                "counts": cumulative,
                "sum": self.sum_,
                "count": self.count,
            }


class MetricRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], Counter] = {}
        self._gauges: dict[tuple[str, tuple], Gauge] = {}
        self._histograms: dict[tuple[str, tuple], Histogram] = {}

    def counter(self, name: str, labels: dict | None = None) -> Counter:
        key = (name, _labels_key(labels))
        with self._lock:
            if key not in self._counters:
                self._counters[key] = Counter(name=name, labels=dict(labels or {}))
            return self._counters[key]

    def gauge(self, name: str, labels: dict | None = None) -> Gauge:
        key = (name, _labels_key(labels))
        with self._lock:
            if key not in self._gauges:
                self._gauges[key] = Gauge(name=name, labels=dict(labels or {}))
            return self._gauges[key]

    def histogram(self, name: str, labels: dict | None = None, buckets: Iterable[float] | None = None) -> Histogram:
        key = (name, _labels_key(labels))
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = Histogram(
                    name=name,
                    labels=dict(labels or {}),
                    buckets=tuple(buckets) if buckets else DEFAULT_BUCKETS,
                )
            return self._histograms[key]

    def snapshot(self) -> list[dict]:
        with self._lock:
            out = []
            for c in self._counters.values():
                out.append(c.snapshot())
            for g in self._gauges.values():
                out.append(g.snapshot())
            for h in self._histograms.values():
                out.append(h.snapshot())
            return out

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


_registry: MetricRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> MetricRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = MetricRegistry()
        return _registry


def set_registry(reg: MetricRegistry) -> None:
    global _registry
    with _registry_lock:
        _registry = reg
