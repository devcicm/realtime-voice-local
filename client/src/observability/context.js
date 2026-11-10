// Trace context — propagates trace_id/span_id across async boundaries.
//
// In the browser there's no contextvars, so we use module-scoped state.
// Each spawn() captures the current context and restores it inside the
// callback, so nested async work inherits the right trace.

let _traceId = '';
let _spanId = '';
let _parentSpanId = '';

export function getCurrent() {
  return { traceId: _traceId, spanId: _spanId, parentSpanId: _parentSpanId };
}

export function setCurrent(ctx) {
  _traceId = ctx.traceId || '';
  _spanId = ctx.spanId || '';
  _parentSpanId = ctx.parentSpanId || '';
}

export function newTraceId() {
  // crypto.randomUUID is broadly available; keep 16-hex fallback.
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

export function newSpanId() {
  return newTraceId().slice(0, 8);
}

export function headerDict() {
  if (!_traceId) return {};
  return { 'X-Trace-Id': _traceId, 'X-Span-Id': _spanId };
}

export async function withContext(ctx, fn) {
  const prev = getCurrent();
  setCurrent({ ...prev, ...ctx });
  try {
    return await fn();
  } finally {
    setCurrent(prev);
  }
}

// Wraps a fetch so it inherits the current trace and propagates it.
export async function tracedFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}), ...headerDict() };
  const res = await fetch(url, { ...opts, headers });
  // Inherit server-assigned trace_id if the server created a new one.
  if (res.headers.get('X-Trace-Id')) {
    _traceId = res.headers.get('X-Trace-Id');
  }
  return res;
}
