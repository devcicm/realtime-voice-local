// Vitest tests for the STT debouncer.
import { describe, it, expect, vi } from 'vitest';
import { SttDebouncer } from '../src/audio/sttDebouncer.js';

function makeClock() {
  let now = 0;
  return {
    now: () => now,
    advance: (ms) => { now += ms; },
    set: (t) => { now = t; },
  };
}

function makeScheduler() {
  const timers = new Map();
  let nextId = 1;
  return {
    schedule: (ms, fn) => {
      const id = nextId++;
      timers.set(id, { delay: ms, fn, fired: false });
      return id;
    },
    fire: (id) => {
      const t = timers.get(id);
      if (t && !t.fired) {
        t.fired = true;
        t.fn();
      }
    },
    pending: () => [...timers.values()].filter(t => !t.fired),
  };
}

describe('SttDebouncer', () => {
  it('commits after debounceMs of silence', () => {
    const clock = makeClock();
    const sched = makeScheduler();
    const commits = [];
    const d = new SttDebouncer({
      debounceMs: 1500,
      onCommit: (text) => commits.push(text),
      now: clock.now,
      schedule: sched.schedule,
      clearTimer: () => {},  // ignore for this test
    });
    d.pushPartial('hola');
    expect(sched.pending().length).toBe(1);
    // Fire the timer.
    sched.fire(1);
    expect(commits).toEqual(['hola']);
  });

  it('replaces the buffer with each new partial and resets the timer', () => {
    const clock = makeClock();
    const sched = makeScheduler();
    const commits = [];
    const fired = [];
    const d = new SttDebouncer({
      debounceMs: 1500,
      onCommit: (text) => commits.push(text),
      onPartial: (text) => fired.push(text),
      now: clock.now,
      schedule: sched.schedule,
      clearTimer: (id) => { fired.push('cancel:' + id); },
    });
    d.pushPartial('h');
    d.pushPartial('ho');
    d.pushPartial('hola');
    // Each push should clear the previous timer and schedule a new one.
    expect(fired.filter(x => x.startsWith('cancel:')).length).toBe(2);
    // Only the last partial is buffered.
    expect(d.pending).toBe('hola');
  });

  it('commit() forces a flush', () => {
    const clock = makeClock();
    const sched = makeScheduler();
    const commits = [];
    const d = new SttDebouncer({
      debounceMs: 1500,
      onCommit: (text) => commits.push(text),
      now: clock.now,
      schedule: sched.schedule,
      clearTimer: () => {},
    });
    d.pushPartial('hola');
    d.commit();
    expect(commits).toEqual(['hola']);
    expect(d.pending).toBe('');
  });

  it('cancel() discards the buffer', () => {
    const commits = [];
    const cancels = [];
    const d = new SttDebouncer({
      debounceMs: 1500,
      onCommit: (text) => commits.push(text),
      onCancel: () => cancels.push(true),
      now: () => 0,
      schedule: () => 1,
      clearTimer: () => {},
    });
    d.pushPartial('hola');
    d.cancel();
    expect(commits).toEqual([]);
    expect(cancels).toEqual([true]);
    expect(d.pending).toBe('');
  });
});
