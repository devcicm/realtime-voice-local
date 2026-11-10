"""OTLP exporter — OpenTelemetry-compatible HTTP exporter.

Posts metric snapshots to an OTLP/HTTP endpoint (e.g. a local OTel
Collector or vendor that supports the OTLP/HTTP protocol).

The payload format mirrors the OpenTelemetry Metrics Data Model:

  {
    "resourceMetrics": [{
      "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "..."}}]},
      "scopeMetrics": [{
        "scope": {"name": "kokoro-tts"},
        "metrics": [
          {"name": "stt.transcribe.elapsed_ms", "unit": "ms",
           "histogram": {"dataPoints": [...]}},
          ...
        ]
      }]
    }]
  }

This is a *minimal* implementation — sufficient for getting metrics
into a real OTel backend without requiring the opentelemetry-* libs.
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Optional

from .exporters import Exporter
from .metrics import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


def _to_otlp(snapshot: list[dict], service_name: str = "kokoro-tts") -> dict:
    metrics = []
    for m in snapshot:
        name = m["name"]
        labels = m.get("labels", {})
        otlp_labels = [{"key": k, "value": {"stringValue": str(v)}} for k, v in labels.items()]

        if m["type"] == "counter":
            metrics.append({
                "name": name,
                "sum": {"dataPoints": [{
                    "asInt": str(int(m["value"])),
                    "attributes": otlp_labels,
                }]},
            })
        elif m["type"] == "gauge":
            metrics.append({
                "name": name,
                "gauge": {"dataPoints": [{
                    "asDouble": float(m["value"]),
                    "attributes": otlp_labels,
                }]},
            })
        elif m["type"] == "histogram":
            metrics.append({
                "name": name,
                "unit": "ms",
                "histogram": {"dataPoints": [{
                    "count": str(m["count"]),
                    "sum": float(m["sum"]),
                    "bucketCounts": [str(c) for c in m["counts"]],
                    "explicitBounds": [float(b) for b in m["buckets"] if b != "+Inf"],
                    "attributes": otlp_labels,
                }]},
            })

    return {
        "resourceMetrics": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": service_name}},
                ]
            },
            "scopeMetrics": [{
                "scope": {"name": "kokoro-tts"},
                "metrics": metrics,
            }],
        }]
    }


class OtlpHttpExporter(Exporter):
    """Post snapshots to an OTLP/HTTP endpoint.

    Endpoint format: ``http://host:port/v1/metrics`` (standard OTLP path).
    """

    def __init__(self, endpoint: str, *, service_name: str = "kokoro-tts", timeout: float = 5.0) -> None:
        self._endpoint = endpoint
        self._service_name = service_name
        self._timeout = timeout

    def export(self, snapshot: list[dict], ts: str) -> None:
        payload = _to_otlp(snapshot, self._service_name)
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status >= 400:
                    logger.warning("OTLP endpoint returned %d", resp.status)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            logger.warning("OTLP export failed: %s", e)

    def close(self) -> None:
        pass
