// Vitest tests for the SessionContext.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { SessionContext, getSessionContext, resetSessionContext } from '../src/session/context.js';

describe('SessionContext', () => {
  beforeEach(() => {
    resetSessionContext();
  });

  it('is empty by default', () => {
    const ctx = new SessionContext();
    expect(ctx.isEmpty()).toBe(true);
    expect(ctx.toSystemContext()).toBe('');
  });

  it('toSystemContext returns a block when state is set', () => {
    const ctx = new SessionContext();
    ctx.setVadActive(true);
    const out = ctx.toSystemContext();
    expect(out).toContain('[Session context]');
    expect(out).toContain('VAD: user is currently speaking');
  });

  it('recent interruption is shown for < 60s', () => {
    const ctx = new SessionContext();
    ctx.setInterruption('barge-in');
    const out = ctx.toSystemContext();
    expect(out).toContain('Last interruption');
    expect(out).toContain('barge-in');
  });

  it('old interruption is not shown', () => {
    const ctx = new SessionContext();
    ctx.setInterruption('barge-in');
    // Force the ts to be old.
    ctx._lastInterruption.ts = performance.now() - 70000;
    const out = ctx.toSystemContext();
    expect(out).not.toContain('Last interruption');
  });

  it('stt partial is shown truncated to 60 chars', () => {
    const ctx = new SessionContext();
    const longText = 'a'.repeat(80);
    ctx.setSttPartial(longText);
    const out = ctx.toSystemContext();
    expect(out).toContain('STT partial');
    expect(out).toContain('...');
  });

  it('transport is included in the block', () => {
    const ctx = new SessionContext();
    ctx.setTransport('webrtc');
    ctx.setVadActive(true);
    expect(ctx.toSystemContext()).toContain('Transport: webrtc');
  });

  it('reset() empties everything', () => {
    const ctx = new SessionContext();
    ctx.setVadActive(true);
    ctx.setUserSpeaking(true);
    ctx.setSttPartial('hola');
    ctx.setInterruption('x');
    ctx.reset();
    expect(ctx.isEmpty()).toBe(true);
  });

  it('listeners are notified on setX', () => {
    const ctx = new SessionContext();
    const cb = vi.fn();
    ctx.onChange(cb);
    ctx.setVadActive(true);
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it('toJson is a serializable snapshot', () => {
    const ctx = new SessionContext();
    ctx.setTurn('model_speaking');
    const json = ctx.toJson();
    expect(json.turn).toBe('model_speaking');
    expect(JSON.stringify(json)).toBeTruthy();
  });
});
