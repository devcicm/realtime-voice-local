"""Integration test for the TraceMiddleware on a real FastAPI app.

Boots a minimal FastAPI app with the middleware, makes HTTP requests,
and asserts that:
  - X-Trace-Id is generated when no header is supplied.
  - X-Trace-Id is preserved when supplied.
  - The handler can read the trace context via get_current().
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.shared.observability import TraceMiddleware, get_current


@pytest.fixture
def app():
    app = FastAPI()
    app.add_middleware(TraceMiddleware)

    @app.get("/echo")
    async def echo():
        ctx = get_current()
        return {
            "trace_id": ctx["trace_id"],
            "span_id": ctx["span_id"],
            "parent_span_id": ctx["parent_span_id"],
        }

    @app.get("/server-trace")
    async def server_trace():
        # Endpoint that creates its own trace (no incoming header).
        ctx = get_current()
        return {"trace_id": ctx["trace_id"]}

    return app


def test_generates_trace_id_when_missing(app):
    client = TestClient(app)
    res = client.get("/echo")
    assert res.status_code == 200
    body = res.json()
    # Body should show a generated trace_id.
    assert body["trace_id"] != ""
    # Header should echo it back.
    assert res.headers["X-Trace-Id"] == body["trace_id"]
    assert "X-Span-Id" in res.headers


def test_preserves_incoming_trace_id(app):
    client = TestClient(app)
    incoming = "trace-from-client-12345"
    res = client.get("/echo", headers={"X-Trace-Id": incoming})
    assert res.status_code == 200
    body = res.json()
    assert body["trace_id"] == incoming
    assert res.headers["X-Trace-Id"] == incoming


def test_propagates_span_id_to_handler(app):
    client = TestClient(app)
    res = client.get("/echo", headers={"X-Trace-Id": "abc", "X-Span-Id": "parent-span"})
    body = res.json()
    assert body["trace_id"] == "abc"
    # The handler's span_id comes from the middleware, not the request.
    assert body["span_id"] != "parent-span"
    assert body["parent_span_id"] == "parent-span"


def test_handler_can_observe_trace_context(app):
    """Handler-side usage: code that doesn't know about the request can still read the trace."""
    from server.shared.observability import get_tracer

    observed = {}

    @app.get("/with-span")
    async def with_span():
        with get_tracer().span("nested") as sp:
            observed["trace_id"] = sp.trace_id
            observed["events"] = []
            sp.event("inside-nested")
            observed["events"].append(sp.events[0].name)
        return {"ok": True}

    client = TestClient(app)
    res = client.get("/with-span", headers={"X-Trace-Id": "outer-trace"})
    assert res.status_code == 200
    # The nested span inherits the trace from the middleware.
    assert observed["trace_id"] == "outer-trace"
    assert observed["events"] == ["inside-nested"]
