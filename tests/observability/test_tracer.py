"""Tests for the server Tracer."""
import pytest
from server.shared.observability.context import (
    trace_ctx_var, get_current, trace_context, new_trace_id, header_dict,
)
from server.shared.observability.tracer import Tracer, get_tracer, SpanCollector


def test_default_context_is_empty():
    # Not in a trace context — values are empty strings.
    ctx = get_current()
    assert ctx["trace_id"] == ""
    assert ctx["span_id"] == ""


def test_trace_context_manager():
    with trace_context("trace-123", "span-1") as ctx:
        assert ctx["trace_id"] == "trace-123"
        assert ctx["span_id"] == "span-1"
        # Inside the block, get_current() returns the same.
        assert get_current()["trace_id"] == "trace-123"
    # After the block, restored.
    assert get_current()["trace_id"] == ""


def test_nested_trace_context():
    with trace_context("outer", "outer-span"):
        assert get_current()["trace_id"] == "outer"
        with trace_context("inner", "inner-span"):
            assert get_current()["trace_id"] == "inner"
        # Inner has exited; outer is restored.
        assert get_current()["trace_id"] == "outer"


def test_header_dict():
    with trace_context("abc") as ctx:
        h = header_dict()
        assert h["X-Trace-Id"] == "abc"
        assert "X-Span-Id" in h


def test_header_dict_empty_outside_context():
    assert header_dict() == {}


def test_tracer_basic_span():
    tracer = Tracer()
    with tracer.span("test.span", {"foo": "bar"}) as sp:
        assert sp.name == "test.span"
        assert sp.attributes["foo"] == "bar"
        assert sp.status == "ok"
        sp.event("checkpoint", {"step": 1})
    # Span was finished — last in collector.
    finished = tracer.collector.recent(1)
    assert finished[0]["name"] == "test.span"
    assert finished[0]["status"] == "ok"
    assert finished[0]["events"][0]["name"] == "checkpoint"
    assert finished[0]["attributes"]["foo"] == "bar"


def test_tracer_inherits_context():
    tracer = Tracer()
    with trace_context("parent-trace", "parent-span"):
        with tracer.span("child") as sp:
            assert sp.trace_id == "parent-trace"
            assert sp.parent_span_id == "parent-span"
    finished = tracer.collector.recent(1)
    assert finished[0]["trace_id"] == "parent-trace"
    assert finished[0]["parent_span_id"] == "parent-span"


def test_tracer_records_error():
    tracer = Tracer()
    with pytest.raises(ValueError):
        with tracer.span("will.fail") as sp:
            raise ValueError("boom")
    finished = tracer.collector.recent(1)
    assert finished[0]["status"] == "error"
    assert finished[0]["attributes"]["error.type"] == "ValueError"
    assert finished[0]["attributes"]["error.message"] == "boom"


def test_tracer_creates_new_trace_when_no_context():
    tracer = Tracer()
    with tracer.span("orphan") as sp:
        assert sp.trace_id != ""
    finished = tracer.collector.recent(1)
    assert len(finished[0]["trace_id"]) > 0


def test_tracer_collector_by_trace():
    tracer = Tracer()
    with trace_context("trace-x"):
        with tracer.span("a"): pass
        with tracer.span("b"): pass
    with trace_context("trace-y"):
        with tracer.span("c"): pass
    x = tracer.collector.by_trace("trace-x")
    y = tracer.collector.by_trace("trace-y")
    assert {s["name"] for s in x} == {"a", "b"}
    assert {s["name"] for s in y} == {"c"}


def test_get_tracer_returns_singleton():
    a = get_tracer()
    b = get_tracer()
    assert a is b


def test_collector_caps_at_max():
    collector = SpanCollector(max_size=5)
    tracer = Tracer(collector)
    for i in range(10):
        with tracer.span(f"span-{i}"): pass
    recent = tracer.collector.recent(20)
    assert len(recent) == 5
    assert recent[0]["name"] == "span-5"
    assert recent[-1]["name"] == "span-9"
