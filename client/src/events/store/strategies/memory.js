// MemoryStrategy — in-memory event repository for the browser.

import { EventRepository } from './repository.js';

export class MemoryStrategy extends EventRepository {
  constructor(maxSize = 1000) {
    super();
    this._events = [];
    this._byId = new Map();
    this._seq = 0;
    this._max = maxSize;
  }

  async append(event) {
    this._seq += 1;
    const stored = { ...event, seq: this._seq };
    this._events.push(stored);
    this._byId.set(stored.id, stored);
    if (this._events.length > this._max) {
      const dropped = this._events.splice(0, this._events.length - this._max);
      for (const e of dropped) this._byId.delete(e.id);
    }
    return stored;
  }
  async getById(id) { return this._byId.get(id) || null; }
  async getByCorrelationId(cid, limit = 1000) {
    return this._events.filter(e => e.correlation_id === cid).slice(0, limit);
  }
  async getByTraceId(tid, limit = 1000) {
    return this._events.filter(e => e.trace?.trace_id === tid).slice(0, limit);
  }
  async getByType(type, limit = 1000) {
    return this._events.filter(e => e.type === type).slice(-limit);
  }
  async getSince(seq, limit = 1000) {
    return this._events.filter(e => e.seq > seq).slice(0, limit);
  }
  async latestSeq() { return this._seq; }
  async count() { return this._events.length; }
  async clear() { this._events = []; this._byId.clear(); this._seq = 0; }
  async close() { await this.clear(); }
}
