// Tests for FencedBus.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { FencedBus } from '../../src/realtime/fencedBus.js';

class FakeBus {
  constructor() {
    this._subs = new Map();
  }
  subscribe(type, handler) {
    if (!this._subs.has(type)) this._subs.set(type, new Set());
    this._subs.get(type).add(handler);
    return () => this._subs.get(type).delete(handler);
  }
  emit(type, payload, opts) {
    const event = { type, payload, opts, ts: Date.now() };
    for (const h of this._subs.get(type) || []) h(event);
    for (const h of this._subs.get('*') || []) h(event);
    return event;
  }
  clear() { this._subs.clear(); }
}

describe('FencedBus', () => {
  let bus, tc, metrics, fb;
  beforeEach(() => {
    bus = new FakeBus();
    tc = { activeRunId: 'run-active' };
    metrics = { stale_events_dropped_total: { inc: vi.fn() } };
    fb = new FencedBus({ bus, turnController: tc, metrics });
  });

  it('passes events with no runId through (back-compat)', () => {
    const seen = [];
    fb.subscribe('llm.token', (e) => seen.push(e));
    bus.emit('llm.token', { delta: 'a' });
    expect(seen.length).toBe(1);
    expect(seen[0].payload.delta).toBe('a');
  });

  it('default fence drops events with mismatched runId', () => {
    const seen = [];
    fb.subscribe('llm.token', (e) => seen.push(e));
    bus.emit('llm.token', { runId: 'run-stale', delta: 'a' });
    bus.emit('llm.token', { runId: 'run-active', delta: 'b' });
    bus.emit('llm.token', { runId: 'run-stale', delta: 'c' });
    expect(seen.map(e => e.payload.delta)).toEqual(['b']);
    expect(metrics.stale_events_dropped_total.inc).toHaveBeenCalledTimes(2);
  });

  it('runId="all" disables fence', () => {
    const seen = [];
    fb.subscribe('llm.token', (e) => seen.push(e), { runId: 'all' });
    bus.emit('llm.token', { runId: 'run-a', delta: '1' });
    bus.emit('llm.token', { runId: 'run-b', delta: '2' });
    expect(seen.length).toBe(2);
  });

  it('specific runId fence accepts only matching events', () => {
    const seen = [];
    fb.subscribe('llm.token', (e) => seen.push(e), { runId: 'run-x' });
    bus.emit('llm.token', { runId: 'run-x', delta: '1' });
    bus.emit('llm.token', { runId: 'run-y', delta: '2' });
    expect(seen.length).toBe(1);
  });

  it('emit injects activeRunId and sessionId/turnId', () => {
    const seen = [];
    fb.subscribe('*', (e) => seen.push(e), { runId: 'all' });
    tc.activeRun = { runId: 'r-1', sessionId: 's-1', turnId: 't-1' };
    fb.emit('llm.token', { delta: 'a' });
    expect(seen[0].payload.runId).toBe('r-1');
    expect(seen[0].payload.sessionId).toBe('s-1');
    expect(seen[0].payload.turnId).toBe('t-1');
  });

  it('emit does not override explicit runId', () => {
    const seen = [];
    fb.subscribe('*', (e) => seen.push(e), { runId: 'all' });
    tc.activeRunId = 'r-active';
    fb.emit('llm.token', { runId: 'r-explicit', delta: 'a' });
    expect(seen[0].payload.runId).toBe('r-explicit');
  });

  it('handler errors do not break the bus', () => {
    fb.subscribe('llm.token', () => { throw new Error('boom'); });
    fb.subscribe('llm.token', (e) => seen.push(e));
    const seen = [];
    expect(() => bus.emit('llm.token', { runId: 'run-active', delta: 'a' })).not.toThrow();
  });

  it('dispose unsubscribes everything', () => {
    let count = 0;
    fb.subscribe('a', () => count++);
    fb.subscribe('b', () => count++);
    fb.dispose();
    bus.emit('a', { runId: 'r' });
    bus.emit('b', { runId: 'r' });
    expect(count).toBe(0);
  });
});
