"""Tests for metrics + exporters."""
import json
import tempfile
from pathlib import Path

import pytest

from server.shared.observability.metrics import (
    MetricRegistry, Counter, Gauge, Histogram, get_registry, set_registry,
    DEFAULT_BUCKETS,
)
from server.shared.observability.exporters import (
    MemoryExporter, JsonlFileExporter, Flusher,
)


@pytest.fixture
def fresh_registry():
    reg = MetricRegistry()
    set_registry(reg)
    yield reg
    set_registry(MetricRegistry())


def test_counter_inc(fresh_registry):
    c = fresh_registry.counter("chat.sent")
    c.inc()
    c.inc(2)
    assert c.value == 3
    snap = c.snapshot()
    assert snap["type"] == "counter"
    assert snap["value"] == 3


def test_counter_with_labels(fresh_registry):
    a = fresh_registry.counter("tts.requests", {"voice": "ef_dora"})
    b = fresh_registry.counter("tts.requests", {"voice": "em_alex"})
    a.inc(5)
    b.inc(2)
    assert a.value == 5
    assert b.value == 2
    # Same metric name but distinct label sets.
    assert a is not b


def test_labeled_counter_is_cached(fresh_registry):
    a = fresh_registry.counter("foo", {"x": "1"})
    b = fresh_registry.counter("foo", {"x": "1"})
    assert a is b


def test_gauge_set_inc_dec(fresh_registry):
    g = fresh_registry.gauge("ws.connections")
    g.set(5)
    g.inc()
    g.dec(2)
    assert g.value == 4


def test_histogram_observe_default_buckets(fresh_registry):
    h = fresh_registry.histogram("tts.first_byte_ms")
    for v in [5, 60, 80, 200, 600, 9999]:
        h.observe(v)
    snap = h.snapshot()
    assert snap["count"] == 6
    assert snap["sum"] == pytest.approx(5 + 60 + 80 + 200 + 600 + 9999)
    # Cumulative counts: each observation falls into the bucket of its value
    # AND every higher bucket (Prometheus convention).
    counts = snap["counts"]
    # Bucket "10" should have just the 5.
    assert counts[0] == 1
    # Bucket "100" cumulative includes 5, 60, 80.
    assert counts[3] == 3
    # Bucket "1000" cumulative includes 5, 60, 80, 200, 600.
    assert counts[6] == 5
    # Bucket "10000" cumulative includes everything (9999 ≤ 10000).
    assert counts[-2] == 6
    assert counts[-1] == 6  # +Inf


def test_histogram_custom_buckets(fresh_registry):
    h = fresh_registry.histogram("custom", buckets=[1, 2, 3])
    h.observe(0.5)
    h.observe(2.5)
    h.observe(5)
    snap = h.snapshot()
    # Per-bucket: [1, 0, 1, 1] (0.5→1, 2.5→3, 5→+Inf)
    # Cumulative: [1, 1, 2, 3]
    assert snap["counts"] == [1, 1, 2, 3]


def test_registry_snapshot_includes_all():
    reg = MetricRegistry()
    c = reg.counter("c")
    g = reg.gauge("g")
    h = reg.histogram("h")
    c.inc()
    g.set(7)
    h.observe(100)
    snap = reg.snapshot()
    types = {m["type"] for m in snap}
    assert types == {"counter", "gauge", "histogram"}


def test_registry_reset(fresh_registry):
    fresh_registry.counter("x").inc(10)
    fresh_registry.gauge("y").set(99)
    fresh_registry.reset()
    assert fresh_registry.snapshot() == []


def test_get_registry_singleton():
    a = get_registry()
    b = get_registry()
    assert a is b


def test_memory_exporter():
    exp = MemoryExporter(max_size=3)
    exp.export([{"name": "a", "value": 1}], "ts1")
    exp.export([{"name": "a", "value": 2}], "ts2")
    exp.export([{"name": "a", "value": 3}], "ts3")
    exp.export([{"name": "a", "value": 4}], "ts4")
    recent = exp.recent(10)
    assert len(recent) == 3
    assert recent[0]["ts"] == "ts2"
    assert recent[-1]["ts"] == "ts4"


def test_jsonl_exporter(tmp_path):
    path = tmp_path / "metrics.jsonl"
    exp = JsonlFileExporter(path)
    exp.export([{"name": "x", "value": 1}], "ts1")
    exp.export([{"name": "x", "value": 2}], "ts2")
    exp.close()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["ts"] == "ts1"
    assert parsed[1]["metrics"][0]["value"] == 2


def test_flusher_pulls_from_registry_and_calls_exporters():
    reg = MetricRegistry()
    set_registry(reg)
    reg.counter("foo").inc(5)

    captured = []
    mem = MemoryExporter()
    mem.export = lambda snap, ts: captured.append(snap)  # type: ignore

    flusher = Flusher(reg, [mem], interval_ms=10)
    flusher.flush()
    flusher.close()

    assert len(captured) == 1
    assert any(m["name"] == "foo" and m["value"] == 5 for m in captured[0])
