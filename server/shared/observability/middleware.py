"""FastAPI middleware for trace propagation.

Installed on each server (TTS, STT). Reads ``X-Trace-Id`` from the
incoming request, opens a span, propagates the trace to handler code,
and echoes the header back on the response.

Usage::

    from server.shared.observability.middleware import TraceMiddleware
    app.add_middleware(TraceMiddleware)
"""
from __future__ import annotations

import time
import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .context import (
    trace_ctx_var,
    new_trace_id,
    new_span_id,
)
from .tracer import get_tracer

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"
SPAN_HEADER = "X-Span-Id"


class TraceMiddleware(BaseHTTPMiddleware):
    """ASGI middleware that opens a span per HTTP request and propagates the trace.

    Behavior:
      - Reads ``X-Trace-Id`` from request. If absent, generates a new one.
      - Sets the trace contextvar so handlers can read get_current().
      - Wraps the call in a span named "http {METHOD} {path}".
      - Echoes the trace header back on the response.
      - Records ``http.duration_ms`` and ``http.status_code`` as attributes.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming_trace = request.headers.get(TRACE_HEADER)
        incoming_span = request.headers.get(SPAN_HEADER, "")
        trace_id = incoming_trace or new_trace_id()

        method = request.method
        path = request.url.path
        tracer = get_tracer()

        # Seed the context so the http span's parent_span_id is the incoming span.
        seed = {
            "trace_id": trace_id,
            "span_id": incoming_span or "00000000",
            "parent_span_id": "",
        }
        token = trace_ctx_var.set(seed)
        try:
            with tracer.span(
                f"http {method} {path}",
                {
                    "http.method": method,
                    "http.path": path,
                    "trace.inherited": bool(incoming_trace),
                },
            ) as span:
                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
                response.headers[TRACE_HEADER] = trace_id
                response.headers[SPAN_HEADER] = span.span_id
                return response
        except Exception:
            logger.exception("request error: %s %s", method, path)
            raise
        finally:
            trace_ctx_var.reset(token)
