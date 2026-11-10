// Tracer — minimal span-based tracing for the browser.
//
// Mirrors server/shared/observability/tracer.py.
//
//   const span = tracer.startSpan('chat.send', { model: 'qwen' });
//   span.event('llm.ttft', { value_ms: 120 });
//   span.finish('ok');
//
// Or as a context manager (recommended):
//
//   withSpan('chat.send', { model: 'qwen' }, (span) => {
//     span.event('llm.ttft', { value_ms: 120 });
//   });

import { getCurrent, setCurrent, newTraceId, newSpanId } from './context.js';

class SpanEvent {
  constructor(name, attributes = {}) {
    this.name = name;
    this.ts = Date.now();
    this.tsMs = performance.now();
    this.attributes = attributes;
  }
}

export class Span {
  constructor({ traceId, spanId, parentSpanId, name, attributes }) {
    this.traceId = traceId;
    this.spanId = spanId;
    this.parentSpanId = parentSpanId;
    this.name = name;
    this.startMs = performance.now();
    this.endMs = 0;
    this.attributes = { ...(attributes || {}) };
    this.events = [];
    this.status = 'ok';
  }

  event(name, attributes = {}) { this.events.push(new SpanEvent(name, attributes)); }
  setAttribute(k, v) { this.attributes[k] = v; }
  setStatus(s) { this.status = s; }
  durationMs() { return Math.max(0, this.endMs - this.startMs); }

  toJSON() {
    return {
      trace_id: this.traceId,
      span_id: this.spanId,
      parent_span_id: this.parentSpanId,
      name: this.name,
      start_ms: this.startMs,
      end_ms: this.endMs,
      duration_ms: this.durationMs(),
      status: this.status,
      attributes: { ...this.attributes },
      events: this.events.map(e => ({
        name: e.name, ts_ms: e.tsMs, attributes: { ...e.attributes },
      })),
    };
  }

  finish(status = 'ok') {
    if (this.status === 'ok') this.status = status;
    this.endMs = performance.now();
    SpanCollector._global?.onFinish(this);
  }
}

class SpanCollector {
  constructor() {
    this.spans = [];
    this.max = 1000;
    this.listeners = new Set();
  }
  onFinish(span) {
    this.spans.push(span);
    if (this.spans.length > this.max) this.spans = this.spans.slice(-this.max);
    for (const l of this.listeners) {
      try { l(span); } catch (_) { /* ignore */ }
    }
  }
  recent(n = 50) { return this.spans.slice(-n).map(s => s.toJSON()); }
  byTrace(traceId) { return this.spans.filter(s => s.traceId === traceId).map(s => s.toJSON()); }
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
}
SpanCollector._global = null;

export class Tracer {
  constructor() {
    this.collector = new SpanCollector();
    SpanCollector._global = this.collector;
  }

  /**
   * Open a span. Uses the current trace context if present.
   * Returns a Span; caller must call span.finish(...).
   */
  startSpan(name, attributes) {
    const current = getCurrent();
    const traceId = current.traceId || newTraceId();
    const parentSpanId = current.spanId;
    const spanId = newSpanId();
    const span = new Span({ traceId, spanId, parentSpanId, name, attributes });
    setCurrent({ traceId, spanId, parentSpanId });
    return span;
  }

  /**
   * Wrap a function in a span. Equivalent to:
   *   const sp = tracer.startSpan(name, attrs);
   *   try { return await fn(sp); } finally { sp.finish(); }
   */
  async withSpan(name, attributes, fn) {
    const span = this.startSpan(name, attributes);
    try {
      return await fn(span);
    } catch (err) {
      span.setStatus('error');
      span.setAttribute('error.type', err?.name || 'Error');
      span.setAttribute('error.message', err?.message || String(err));
      throw err;
    } finally {
      span.finish();
    }
  }
}

let _tracer = null;
export function getTracer() {
  if (!_tracer) _tracer = new Tracer();
  return _tracer;
}
export function setTracer(t) { _tracer = t; }
