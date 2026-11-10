// Event envelope — canonical shape for everything that flows through the bus.
// Mirrors server/shared/events/envelope.py.

function _newId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

export class Event {
  constructor({
    type,
    source,
    payload = {},
    id = _newId(),
    ts = new Date().toISOString(),
    tsMs = performance.now(),
    correlationId = null,
    causationId = null,
    trace = null,
    attributes = {},
  }) {
    this.id = id;
    this.type = type;
    this.source = source;
    this.payload = payload;
    this.ts = ts;
    this.tsMs = tsMs;
    this.correlationId = correlationId;
    this.causationId = causationId;
    this.trace = trace;
    this.attributes = attributes;
  }

  toJSON() {
    return {
      id: this.id,
      type: this.type,
      source: this.source,
      payload: this.payload,
      ts: this.ts,
      ts_ms: this.tsMs,
      correlation_id: this.correlationId,
      causation_id: this.causationId,
      trace: this.trace,
      attributes: this.attributes,
    };
  }

  clone(overrides = {}) {
    return new Event({ ...this.toJSON(), ...overrides });
  }
}
