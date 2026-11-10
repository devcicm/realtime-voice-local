// EventStore — high-level wrapper around a Strategy.
// Mirrors server/shared/events/store/__init__.py.

import { MemoryStrategy } from './strategies/memory.js';

export class EventStore {
  constructor(repository, snapshotInterval = 100) {
    this._repo = repository;
    this._snapshotInterval = snapshotInterval;
    this._lastSnapshotSeq = 0;
    this._subscribers = new Set();
  }

  static memory(maxSize = 1000) {
    return new EventStore(new MemoryStrategy(maxSize));
  }

  // ---- write ----
  async append(event) {
    const stored = await this._repo.append(event);
    if (stored.seq % this._snapshotInterval === 0) {
      this._lastSnapshotSeq = stored.seq;
    }
    for (const cb of this._subscribers) {
      try { cb(stored); } catch (_) { /* ignore */ }
    }
    return stored;
  }

  // ---- read ----
  async getById(id) { return this._repo.getById(id); }
  async getByCorrelationId(cid, limit = 1000) { return this._repo.getByCorrelationId(cid, limit); }
  async getByTraceId(tid, limit = 1000) { return this._repo.getByTraceId(tid, limit); }
  async getByType(type, limit = 1000) { return this._repo.getByType(type, limit); }
  async getSince(seq, limit = 1000) { return this._repo.getSince(seq, limit); }
  async latestSeq() { return this._repo.latestSeq(); }
  async count() { return this._repo.count(); }

  // ---- event sourcing ----
  async fold(initialState, reducers, opts = {}) {
    const { sinceSeq = 0, limit = 10000 } = opts;
    const events = await this.getSince(sinceSeq, limit);
    let state = initialState;
    for (const e of events) {
      const r = reducers[e.type];
      if (r) state = r(state, e);
    }
    return state;
  }

  async clear() {
    await this._repo.clear();
    this._lastSnapshotSeq = 0;
  }
  async close() { await this._repo.close(); }

  // ---- subscriptions ----
  subscribe(cb) {
    this._subscribers.add(cb);
    return () => this._subscribers.delete(cb);
  }
}
