// Tests for RunContext and TurnController (Vitest).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { RunContext, RunState } from '../../src/realtime/runContext.js';
import { TurnController } from '../../src/realtime/turnController.js';

describe('RunContext', () => {
  it('starts in CREATED state with unique runId', () => {
    const a = new RunContext();
    const b = new RunContext();
    expect(a.state).toBe(RunState.CREATED);
    expect(b.state).toBe(RunState.CREATED);
    expect(a.runId).not.toBe(b.runId);
    expect(a.runId).toMatch(/^run-/);
  });

  it('follows valid transitions', () => {
    const r = new RunContext();
    r.transition(RunState.THINKING);
    r.transition(RunState.SPEAKING);
    r.transition(RunState.COMPLETED);
    expect(r.state).toBe(RunState.COMPLETED);
  });

  it('rejects invalid transitions without throwing', () => {
    const r = new RunContext();
    // From CREATED, 'bogus' is not a valid target.
    const result = r.transition('bogus-state');
    expect(result).toBe(false);
    expect(r.state).toBe(RunState.CREATED);
  });

  it('transition is idempotent (same state = no-op)', () => {
    const r = new RunContext();
    r.transition(RunState.THINKING);
    r.transition(RunState.THINKING);
    expect(r.state).toBe(RunState.THINKING);
  });

  it('abort can be called multiple times safely', () => {
    const r = new RunContext();
    r.abort();
    r.abort();
    r.abort();
    expect(r.abortController.signal.aborted).toBe(true);
  });

  it('notifies listeners on transitions', () => {
    const r = new RunContext();
    const cb = vi.fn();
    r.onTransition(cb);
    r.transition(RunState.THINKING);
    r.transition(RunState.SPEAKING);
    expect(cb).toHaveBeenCalledTimes(2);
    expect(cb).toHaveBeenLastCalledWith(
      expect.objectContaining({ from: RunState.THINKING, to: RunState.SPEAKING })
    );
  });

  it('isolates listener errors', () => {
    const r = new RunContext();
    r.onTransition(() => { throw new Error('boom'); });
    r.onTransition(() => {});   // should still fire
    expect(() => r.transition(RunState.THINKING)).not.toThrow();
  });

  it('tracks generated and played text', () => {
    const r = new RunContext();
    r.appendGenerated('Hola');
    r.appendGenerated(' mundo');
    r.appendPlayedAudio(500);
    expect(r.generatedText).toBe('Hola mundo');
    expect(r.playedAudioMs).toBe(500);
  });
});

describe('TurnController', () => {
  let bus, log, ctrl, onInterrupt;

  beforeEach(() => {
    bus = { on: vi.fn() };
    log = { truncate: vi.fn(), mark_interrupted: vi.fn() };
    onInterrupt = vi.fn().mockResolvedValue(undefined);
    ctrl = new TurnController({ bus, conversationLog: log, onInterrupt });
  });

  it('starts with no active run', () => {
    expect(ctrl.activeRun).toBeNull();
    expect(ctrl.activeRunId).toBeNull();
  });

  it('newRun creates an active run', () => {
    const run = ctrl.newRun({ turnId: 'turn-1' });
    expect(ctrl.activeRun).toBe(run);
    expect(ctrl.activeRunId).toBe(run.runId);
    expect(run.state).toBe(RunState.CREATED);
  });

  it('interrupt is a no-op when there is no active run', async () => {
    const result = await ctrl.interrupt();
    expect(result).toBe(false);
  });

  it('interrupt aborts the run and fires onInterrupt + truncate + mark_interrupted', async () => {
    const run = ctrl.newRun({ turnId: 'turn-1' });
    run.appendGenerated('Hola mundo');
    run.appendPlayedAudio(800);
    const result = await ctrl.interrupt('barge-in');
    expect(result).toBe(true);
    expect(onInterrupt).toHaveBeenCalledOnce();
    expect(onInterrupt.mock.calls[0][0]).toBe(run);
    expect(onInterrupt.mock.calls[0][1]).toBe('barge-in');
    expect(run.state).toBe(RunState.INTERRUPTED);
    expect(log.truncate).toHaveBeenCalledWith('turn-1', 800);
    expect(log.mark_interrupted).toHaveBeenCalled();
    expect(ctrl.activeRun).toBeNull();
  });

  it('interrupt is idempotent', async () => {
    ctrl.newRun({ turnId: 'turn-1' });
    const a = await ctrl.interrupt('barge-in');
    const b = await ctrl.interrupt('barge-in');
    expect(a).toBe(true);
    expect(b).toBe(true);
    // Only one onInterrupt call.
    expect(onInterrupt).toHaveBeenCalledOnce();
  });

  it('interrupt on completed run is a no-op', async () => {
    const run = ctrl.newRun({ turnId: 'turn-1' });
    run.transition(RunState.THINKING);
    run.transition(RunState.SPEAKING);
    run.transition(RunState.COMPLETED);
    ctrl.complete();
    const result = await ctrl.interrupt('late-barge-in');
    expect(result).toBe(true);  // idempotent: "true because already terminal"
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it('onInterrupt callback errors do not break the cancel chain', async () => {
    ctrl.newRun({ turnId: 'turn-1' });
    const badCtrl = new TurnController({
      bus, conversationLog: log,
      onInterrupt: () => { throw new Error('boom'); },
    });
    badCtrl.newRun({ turnId: 'turn-1' });
    const result = await badCtrl.interrupt('barge-in');
    expect(result).toBe(true);
    expect(log.mark_interrupted).toHaveBeenCalled();
  });

  it('history archives runs in order', async () => {
    const r1 = ctrl.newRun({ turnId: 't1' });
    await ctrl.interrupt('barge-in');
    const r2 = ctrl.newRun({ turnId: 't2' });
    await ctrl.interrupt('barge-in');
    const r3 = ctrl.newRun({ turnId: 't3' });
    await ctrl.interrupt('barge-in');
    expect(ctrl.history.length).toBe(3);
    expect(ctrl.history[0]).toBe(r3);
    expect(ctrl.history[2]).toBe(r1);
  });

  it('newRun forces previous active to INTERRUPTED (defensive)', async () => {
    const r1 = ctrl.newRun({ turnId: 't1' });
    expect(r1.state).toBe(RunState.CREATED);
    const r2 = ctrl.newRun({ turnId: 't2' });
    expect(r1.state).toBe(RunState.INTERRUPTED);
    expect(ctrl.activeRun).toBe(r2);
  });

  it('notifies lifecycle listeners', async () => {
    const events = [];
    ctrl.on((e) => events.push(e.event));
    ctrl.newRun({ turnId: 't1' });
    await ctrl.interrupt('barge-in');
    ctrl.newRun({ turnId: 't2' });
    ctrl.complete();
    expect(events).toEqual(['run.created', 'run.interrupted', 'run.created', 'run.completed']);
  });
});
