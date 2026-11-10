"""Integration test: the metrics endpoint exposed by the servers.

Spins up a FastAPI test client with the TraceMiddleware + a tiny app
that bumps two metrics, then asserts that GET /metrics returns them.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.shared.observability import (
    TraceMiddleware,
    get_registry,
    set_registry,
    get_tracer,
    set_tracer,
    Tracer,
    SpanCollector,
    MetricRegistry,
)


@pytest.fixture
def app():
    reg = MetricRegistry()
    set_registry(reg)
    # Provide a fresh tracer per test so spans are isolated.
    set_tracer(Tracer(SpanCollector(max_size=100)))
    app = FastAPI()
    app.add_middleware(TraceMiddleware)

    @app.get("/do")
    async def do_work():
        get_registry().counter("test.requests").inc()
        get_registry().histogram("test.duration_ms").observe(42)
        return {"ok": True}

    @app.get("/metrics")
    async def metrics():
        return {
            "metrics": get_registry().snapshot(),
            "recent_traces": get_tracer().get_collector().recent(20),
        }

    return app


def test_metrics_endpoint_returns_snapshot(app):
    client = TestClient(app)
    # Hit a few times so counters/histograms have values.
    for _ in range(3):
        client.get("/do")
    res = client.get("/metrics")
    assert res.status_code == 200
    body = res.json()
    metrics = body["metrics"]
    counter = next(m for m in metrics if m["name"] == "test.requests")
    assert counter["value"] == 3
    histogram = next(m for m in metrics if m["name"] == "test.duration_ms")
    assert histogram["count"] == 3
    assert histogram["sum"] == 126  # 42 * 3


def test_metrics_endpoint_includes_traces(app):
    """After hitting an endpoint, the trace collector should have a span."""
    client = TestClient(app)
    client.get("/do", headers={"X-Trace-Id": "test-trace"})
    res = client.get("/metrics")
    traces = res.json()["recent_traces"]
    assert any(t["trace_id"] == "test-trace" for t in traces), f"trace-test not found in {traces}"
