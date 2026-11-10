// Vitest unit tests for sync/renderer, sync/gatekeeper, sync/smoothing.
// Run with: npm test (from client/) or vitest run.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SyncRenderer, WordStatus } from '../src/sync/renderer.js';
import { LLMGatekeeper, GatekeeperModes } from '../src/sync/gatekeeper.js';
import { PlaybackSmoother, SmoothingModes } from '../src/sync/smoothing.js';

// ---- SyncRenderer ----

describe('SyncRenderer', () => {
  let container;

  beforeEach(() => {
    container = document.createElement('div');
  });

  it('renders one span per word', () => {
    new SyncRenderer(container, 'Hola mundo cruel');
    const spans = container.querySelectorAll('.word');
    expect(spans.length).toBe(3);
  });

  it('marks every word as pending initially', () => {
    new SyncRenderer(container, 'uno dos tres');
    const spans = container.querySelectorAll('.word');
    for (const s of spans) expect(s.dataset.status).toBe(WordStatus.PENDING);
  });

  it('marks a word as speaking when elapsed crosses its offset', () => {
    const r = new SyncRenderer(container, 'a bb ccc');
    r.wordOffsetsMs = [0, 200, 500];
    r.audioStartedAtMs = 0;
    // Pretend performance.now() is 250ms after start.
    const origNow = performance.now.bind(performance);
    performance.now = () => 250;
    r._updateHighlights();
    const spans = container.querySelectorAll('.word');
    expect(spans[0].dataset.status).toBe(WordStatus.SPOKEN);  // 0..200 elapsed
    expect(spans[1].dataset.status).toBe(WordStatus.SPEAKING); // 200..500 elapsed
    expect(spans[2].dataset.status).toBe(WordStatus.PENDING);  // 500+
    performance.now = origNow;
  });

  it('marks everything as spoken on audio_end', () => {
    const r = new SyncRenderer(container, 'uno dos tres');
    r.onAudioEnd({ word_offsets_ms: [0, 100, 200], duration_ms: 300 });
    for (const s of container.querySelectorAll('.word')) {
      expect(s.dataset.status).toBe(WordStatus.SPOKEN);
    }
  });

  it('cancel marks words as interrupted from an index', () => {
    const r = new SyncRenderer(container, 'a b c d');
    r.cancel(2);
    const spans = container.querySelectorAll('.word');
    expect(spans[0].dataset.status).toBe(WordStatus.PENDING);
    expect(spans[1].dataset.status).toBe(WordStatus.PENDING);
    expect(spans[2].dataset.status).toBe(WordStatus.INTERRUPTED);
    expect(spans[3].dataset.status).toBe(WordStatus.INTERRUPTED);
  });

  it('setText re-renders', () => {
    const r = new SyncRenderer(container, 'hola');
    r.setText('hola mundo');
    expect(container.querySelectorAll('.word').length).toBe(2);
  });
});

// ---- LLMGatekeeper ----

describe('LLMGatekeeper', () => {
  it('starts unpaused', () => {
    const g = new LLMGatekeeper({ mode: 'balanced' });
    expect(g.paused).toBe(false);
  });

  it('estimates text ms at ~350ms per word', () => {
    expect(LLMGatekeeper.estimateTextMs('uno dos tres')).toBe(1050);
  });

  it('does not pause in free mode', () => {
    const g = new LLMGatekeeper({ mode: 'free' });
    const onPause = vi.fn();
    g.onPause = onPause;
    g.onTextAdded('uno dos tres cuatro cinco seis siete ocho nueve diez');
    expect(onPause).not.toHaveBeenCalled();
  });

  it('pauses in balanced mode when text-ahead exceeds 1500ms', () => {
    const g = new LLMGatekeeper({ mode: 'balanced' });
    const onPause = vi.fn();
    g.onPause = onPause;
    g.onTextAdded('uno dos tres cuatro cinco seis');  // 6 words * 350 = 2100ms
    expect(onPause).toHaveBeenCalledOnce();
    expect(g.paused).toBe(true);
  });

  it('resumes when audio catches up', () => {
    const g = new LLMGatekeeper({ mode: 'balanced' });
    g.onTextAdded('uno dos tres cuatro cinco seis');  // 2100ms text
    expect(g.paused).toBe(true);
    g.onAudioPlayed(2000);  // 2000ms audio drains text-ahead to ~100ms
    expect(g.paused).toBe(false);
  });

  it('strict mode triggers pause sooner', () => {
    const g = new LLMGatekeeper({ mode: 'strict' });
    const onPause = vi.fn();
    g.onPause = onPause;
    g.onTextAdded('uno dos');  // 700ms
    expect(onPause).toHaveBeenCalled();
  });

  it('exposes snapshot for the inspector', () => {
    const g = new LLMGatekeeper({ mode: 'balanced' });
    g.onTextAdded('uno dos');
    const snap = g.snapshot();
    expect(snap.mode).toBe('balanced');
    expect(snap.paused).toBe(false);
    expect(snap.textAheadMs).toBe(700);
  });
});

// ---- PlaybackSmoother ----

describe('PlaybackSmoother', () => {
  it('does not wait for prebuffer in OFF mode', () => {
    const s = new PlaybackSmoother({ mode: 'OFF' });
    expect(s.shouldWaitForPrebuffer([{ durationMs: 10 }], { durationMs: 10 })).toBe(false);
  });

  it('waits for prebuffer in PREBUFFER mode until threshold met', () => {
    const s = new PlaybackSmoother({ mode: 'PREBUFFER', prebufferMs: 200 });
    // 100ms buffered — not enough.
    expect(s.shouldWaitForPrebuffer(
      [{ requestId: 'r1', durationMs: 100 }],
      { requestId: 'r1', durationMs: 100 },
    )).toBe(true);
    // 250ms buffered — enough.
    expect(s.shouldWaitForPrebuffer(
      [{ requestId: 'r1', durationMs: 250 }],
      { requestId: 'r1', durationMs: 250 },
    )).toBe(false);
  });

  it('inserts gap when last request id differs', () => {
    let t = 0;
    const s = new PlaybackSmoother({
      mode: 'GAP',
      gapMs: 80,
      now: () => t,
    });
    s.markPlayed('r1');
    t = 5;  // 5ms since last play
    expect(s.shouldInsertGap({ requestId: 'r2' })).toBe(true);
  });

  it('does not insert gap within the same request', () => {
    let t = 0;
    const s = new PlaybackSmoother({ mode: 'GAP', gapMs: 80, now: () => t });
    s.markPlayed('r1');
    t = 5;
    expect(s.shouldInsertGap({ requestId: 'r1' })).toBe(false);
  });

  it('reset clears playback state', () => {
    const s = new PlaybackSmoother({ mode: 'HYBRID' });
    s.markPlayed('r1');
    s.reset();
    expect(s._lastPlayedAt).toBe(null);
    expect(s._lastRequestId).toBe(null);
  });
});
