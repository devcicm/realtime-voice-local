"""Observability facade — single import for servers.

Submodules:
  - context:  trace context propagation (contextvars + headers)
  - tracer:   span-based tracing
  - metrics:  Counter, Gauge, Histogram + Registry
  - exporters: in-memory, JSONL, stdout sinks + Flusher
  - otlp:     OTLP/HTTP exporter (OpenTelemetry-compatible)
  - middleware: TraceMiddleware for FastAPI
"""
from .context import (
    trace_context,
    trace_ctx_var,
    get_current,
    header_dict,
    new_trace_id,
    new_span_id,
)
from .tracer import Tracer, Span, SpanCollector, get_tracer, set_tracer
from .metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricRegistry,
    get_registry,
    set_registry,
    DEFAULT_BUCKETS,
)
from .exporters import (
    Exporter,
    MemoryExporter,
    JsonlFileExporter,
    StdoutExporter,
    Flusher,
)
from .otlp import OtlpHttpExporter, _to_otlp
from .middleware import TraceMiddleware, TRACE_HEADER, SPAN_HEADER

__all__ = [
    # context
    "trace_context", "trace_ctx_var", "get_current", "header_dict",
    "new_trace_id", "new_span_id",
    # tracer
    "Tracer", "Span", "SpanCollector", "get_tracer", "set_tracer",
    # metrics
    "Counter", "Gauge", "Histogram", "MetricRegistry",
    "get_registry", "set_registry", "DEFAULT_BUCKETS",
    # exporters
    "Exporter", "MemoryExporter", "JsonlFileExporter", "StdoutExporter", "Flusher",
    # otlp
    "OtlpHttpExporter", "_to_otlp",
    # middleware
    "TraceMiddleware", "TRACE_HEADER", "SPAN_HEADER",
]
