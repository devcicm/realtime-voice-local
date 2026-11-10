"""Tests for the OTLP HTTP exporter."""
import json
from unittest.mock import patch, MagicMock
import urllib.error

import pytest

from server.shared.observability import OtlpHttpExporter, _to_otlp


def test_to_otlp_counter():
    snap = [{
        "type": "counter",
        "name": "stt.transcribe.requests",
        "labels": {"model": "tiny"},
        "value": 42,
    }]
    out = _to_otlp(snap)
    metrics = out["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert metrics[0]["name"] == "stt.transcribe.requests"
    dp = metrics[0]["sum"]["dataPoints"][0]
    assert dp["asInt"] == "42"
    assert dp["attributes"][0]["value"]["stringValue"] == "tiny"


def test_to_otlp_gauge():
    snap = [{"type": "gauge", "name": "ws.connections", "labels": {}, "value": 3.5}]
    out = _to_otlp(snap)
    metrics = out["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    assert metrics[0]["gauge"]["dataPoints"][0]["asDouble"] == 3.5


def test_to_otlp_histogram():
    snap = [{
        "type": "histogram",
        "name": "tts.first_byte_ms",
        "labels": {},
        "count": 5,
        "sum": 250.0,
        "buckets": [10, 100, 1000, "+Inf"],
        "counts": [1, 3, 4, 5],  # cumulative
    }]
    out = _to_otlp(snap)
    metrics = out["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    h = metrics[0]["histogram"]["dataPoints"][0]
    assert h["count"] == "5"
    assert h["sum"] == 250.0
    assert h["bucketCounts"] == ["1", "3", "4", "5"]
    # +Inf is excluded from explicit bounds.
    assert h["explicitBounds"] == [10.0, 100.0, 1000.0]


def test_to_otlp_service_name():
    snap = []
    out = _to_otlp(snap, service_name="my-svc")
    attrs = out["resourceMetrics"][0]["resource"]["attributes"]
    assert attrs[0]["value"]["stringValue"] == "my-svc"


def test_otlp_http_exporter_posts_payload():
    captured = []

    def fake_urlopen(req, **kw):
        captured.append((req.full_url, json.loads(req.data.decode())))
        m = MagicMock()
        m.status = 200
        m.__enter__ = lambda self: self
        m.__exit__ = lambda self, *a: False
        return m

    with patch("urllib.request.urlopen", fake_urlopen):
        exporter = OtlpHttpExporter("http://otel.local:4318/v1/metrics")
        exporter.export(
            [{"type": "counter", "name": "x", "labels": {}, "value": 1}],
            "ts-1",
        )

    assert len(captured) == 1
    url, body = captured[0]
    assert url == "http://otel.local:4318/v1/metrics"
    assert body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["name"] == "x"


def test_otlp_http_exporter_swallows_connection_errors():
    """Export failures must not raise — they log and continue."""
    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", boom):
        exporter = OtlpHttpExporter("http://invalid:9999/v1/metrics")
        # Must not raise.
        exporter.export([], "ts-1")
