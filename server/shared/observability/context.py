"""Trace context — propagated via contextvars so any code path can
read the current trace_id/span_id without explicit threading.

This is the West (synchronous) half of the system. Each async task
inherits the context at the moment it was scheduled, so nested calls
inside `async def` see the same trace.

Usage::

    from server.shared.observability.context import trace_ctx_var, get_current, new_span

    @app.middleware("http")
    async def trace_middleware(request, call_next):
        incoming = request.headers.get("X-Trace-Id") or _generate_id()
        token = trace_ctx_var.set({"trace_id": incoming, "span_id": _generate_id()[:8]})
        try:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = incoming
            return response
        finally:
            trace_ctx_var.reset(token)
"""
from __future__ import annotations

import contextvars
import secrets
import time
from contextlib import contextmanager
from typing import Iterator, Optional


def _new_id(size: int = 16) -> str:
    return secrets.token_hex(size)


def _new_span_id() -> str:
    return secrets.token_hex(4)  # 8 chars


# Default empty context — used when no middleware has set one.
_EMPTY: dict = {"trace_id": "", "span_id": "", "parent_span_id": ""}

trace_ctx_var: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "trace_context", default=_EMPTY
)


def get_current() -> dict:
    """Return the trace context active in this call chain."""
    return trace_ctx_var.get()


def new_trace_id() -> str:
    return _new_id(16)


def new_span_id() -> str:
    return _new_span_id()


@contextmanager
def trace_context(trace_id: str, span_id: Optional[str] = None, parent: Optional[str] = None) -> Iterator[dict]:
    """Context manager — set a trace context for the block."""
    ctx = {
        "trace_id": trace_id,
        "span_id": span_id or _new_span_id(),
        "parent_span_id": parent or "",
    }
    token = trace_ctx_var.set(ctx)
    try:
        yield ctx
    finally:
        trace_ctx_var.reset(token)


def header_dict() -> dict:
    """Return the headers to propagate to downstream services."""
    ctx = get_current()
    if not ctx["trace_id"]:
        return {}
    return {
        "X-Trace-Id": ctx["trace_id"],
        "X-Span-Id": ctx["span_id"],
    }
