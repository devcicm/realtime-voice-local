"""Exporters — pluggable sinks for metric snapshots.

Three sinks:
  - MemoryExporter:    ring buffer in memory (default)
  - JsonlFileExporter: append to JSONL file
  - StdoutExporter:    print to stdout (useful for debugging)

A Flusher ties a registry to a list of exporters on a timer.

Usage::

    from server.shared.observability.metrics import get_registry
    from server.shared.observability.exporters import MemoryExporter, JsonlFileExporter, Flusher

    reg = get_registry()
    mem = MemoryExporter(max_size=200)
    jsonl = JsonlFileExporter("./logs/metrics.jsonl")
    flusher = Flusher(reg, [mem, jsonl], interval_ms=5000)
    flusher.start()
"""
from __future__ import annotations

import json
import sys
import threading
import time
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class Exporter(Protocol):
    def export(self, snapshot: list[dict], ts: str) -> None: ...
    def close(self) -> None: ...


class MemoryExporter:
    """Ring buffer of recent snapshots."""

    def __init__(self, max_size: int = 100) -> None:
        self._lock = threading.Lock()
        self._buf: deque = deque(maxlen=max_size)

    def export(self, snapshot: list[dict], ts: str) -> None:
        with self._lock:
            self._buf.append({"ts": ts, "metrics": snapshot})

    def recent(self, n: int = 10) -> list[dict]:
        with self._lock:
            return list(self._buf)[-n:]

    def close(self) -> None:
        pass


class JsonlFileExporter:
    """Append a JSONL line per snapshot."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, snapshot: list[dict], ts: str) -> None:
        line = json.dumps({"ts": ts, "metrics": snapshot}, default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def close(self) -> None:
        pass


class StdoutExporter:
    """Print a compact summary to stdout. For debugging only."""

    def export(self, snapshot: list[dict], ts: str) -> None:
        non_zero = [m for m in snapshot if not _is_zero(m)]
        if not non_zero:
            return
        print(f"[metrics {ts}]", file=sys.stderr)
        for m in non_zero:
            print(f"  {m['name']}({m.get('labels', {})}) = {_summarize(m)}", file=sys.stderr)

    def close(self) -> None:
        pass


def _is_zero(m: dict) -> bool:
    if m["type"] == "histogram":
        return m["count"] == 0
    return m["value"] == 0


def _summarize(m: dict) -> str:
    if m["type"] in ("counter", "gauge"):
        return f"{m['value']}"
    if m["type"] == "histogram":
        if m["count"] == 0:
            return "no observations"
        avg = m["sum"] / m["count"]
        return f"count={m['count']} avg={avg:.1f}ms"
    return "?"


class Flusher:
    """Periodically snap the registry and push to exporters."""

    def __init__(self, registry, exporters: list[Exporter], interval_ms: int = 5000) -> None:
        self._registry = registry
        self._exporters = exporters
        self._interval = interval_ms / 1000.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="metrics-flusher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def flush(self) -> None:
        """One-shot synchronous flush."""
        snapshot = self._registry.snapshot()
        ts = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        for exp in self._exporters:
            try:
                exp.export(snapshot, ts)
            except Exception:
                logger.exception("exporter failed")

    def _run(self) -> None:
        while not self._stop.is_set():
            self.flush()
            self._stop.wait(self._interval)

    def close(self) -> None:
        self.stop()
        for exp in self._exporters:
            try:
                exp.close()
            except Exception:
                pass
