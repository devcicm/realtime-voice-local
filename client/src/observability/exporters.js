// Exporters — pluggable sinks for snapshots.
// Mirrors server/shared/observability/exporters.py.

import { getRegistry } from './metrics.js';

class MemoryExporter {
  constructor(maxSize = 100) {
    this.buf = [];
    this.maxSize = maxSize;
  }
  export(snapshot, ts) {
    this.buf.push({ ts, metrics: snapshot });
    if (this.buf.length > this.maxSize) this.buf = this.buf.slice(-this.maxSize);
  }
  recent(n = 10) { return this.buf.slice(-n); }
  close() {}
}

class ConsoleExporter {
  // For debugging. Group by metric name and log counts.
  export(snapshot, ts) {
    const nz = snapshot.filter(m => !isZero(m));
    if (!nz.length) return;
    console.groupCollapsed?.(`[metrics ${ts}]`);
    for (const m of nz) console.log(m.name, m.labels, summarize(m));
    console.groupEnd?.();
  }
  close() {}
}

function isZero(m) {
  if (m.type === 'histogram') return m.count === 0;
  return m.value === 0;
}
function summarize(m) {
  if (m.type === 'histogram') {
    if (m.count === 0) return 'no observations';
    return `count=${m.count} avg=${(m.sum / m.count).toFixed(1)}ms`;
  }
  return m.value;
}

// POST snapshots to a server endpoint (JSONL, one per snapshot).
class HttpExporter {
  constructor(endpoint, { batchSize = 1, headers = {} } = {}) {
    this.endpoint = endpoint;
    this.batchSize = batchSize;
    this.headers = headers;
    this._batch = [];
  }
  export(snapshot, ts) {
    this._batch.push({ ts, metrics: snapshot });
    if (this._batch.length >= this.batchSize) this._flush();
  }
  _flush() {
    if (!this._batch.length) return;
    const body = JSON.stringify({ snapshots: this._batch });
    this._batch = [];
    // Use sendBeacon if available; else fetch keepalive.
    try {
      if (navigator.sendBeacon) {
        navigator.sendBeacon(this.endpoint, body);
      } else {
        fetch(this.endpoint, {
          method: 'POST',
          body,
          headers: { 'Content-Type': 'application/json', ...this.headers },
          keepalive: true,
        }).catch(() => {});
      }
    } catch (_) { /* ignore */ }
  }
  close() { this._flush(); }
}

class Flusher {
  constructor(registry, exporters, intervalMs = 5000) {
    this.registry = registry;
    this.exporters = exporters;
    this.interval = intervalMs;
    this._timer = null;
  }
  start() {
    if (this._timer) return;
    this._timer = setInterval(() => this.flush(), this.interval);
  }
  stop() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  }
  flush() {
    const snap = this.registry.snapshot();
    const ts = new Date().toISOString();
    for (const exp of this.exporters) {
      try { exp.export(snap, ts); } catch (e) { /* ignore */ }
    }
  }
  close() {
    this.stop();
    for (const exp of this.exporters) try { exp.close(); } catch (_) {}
  }
}

export { MemoryExporter, ConsoleExporter, HttpExporter, Flusher };
