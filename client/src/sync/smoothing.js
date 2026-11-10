// Smoothing — playback gap + prebuffer to avoid audio gaps/clicks.
//
// gap:       pause between consecutive requests (ms)
// prebuffer: wait for N ms of audio buffered before starting playback
//
// The smoother wraps an audio queue and decides when playNextChunk()
// should fire. It's intentionally simple: no adaptive logic, just two
// well-understood knobs.

export const SmoothingModes = {
  OFF:       { useGap: false, usePrebuffer: false, gapMs: 0,    prebufferMs: 0 },
  GAP:       { useGap: true,  usePrebuffer: false, gapMs: 80,   prebufferMs: 0 },
  PREBUFFER: { useGap: false, usePrebuffer: true,  gapMs: 0,    prebufferMs: 200 },
  HYBRID:    { useGap: true,  usePrebuffer: true,  gapMs: 80,   prebufferMs: 200 },
};

export class PlaybackSmoother {
  /**
   * @param {object} opts
   * @param {string} opts.mode         OFF | GAP | PREBUFFER | HYBRID (default HYBRID)
   * @param {number} [opts.gapMs]
   * @param {number} [opts.prebufferMs]
   * @param {function} [opts.schedule]  (delayMs, callback) — setTimeout wrapper
   * @param {function} [opts.now]      () => number         — clock source
   */
  constructor({ mode = 'HYBRID', gapMs, prebufferMs, schedule, now } = {}) {
    const cfg = { ...SmoothingModes[mode] || SmoothingModes.HYBRID };
    if (typeof gapMs === 'number') cfg.gapMs = gapMs;
    if (typeof prebufferMs === 'number') cfg.prebufferMs = prebufferMs;
    this.cfg = cfg;
    this._schedule = schedule || ((ms, fn) => setTimeout(fn, ms));
    this._now = now || (() => performance.now());
    this._lastPlayedAt = null;
    this._lastRequestId = null;
  }

  /** Should the smoother hold playback until more audio is buffered? */
  shouldWaitForPrebuffer(audioQueue, nextItem) {
    if (!this.cfg.usePrebuffer || this.cfg.prebufferMs <= 0) return false;
    if (!nextItem) return false;
    // Count contiguous audio in the queue with the same request_id.
    let total = 0;
    for (const item of audioQueue) {
      if (item.requestId !== nextItem.requestId) break;
      total += item.durationMs || 0;
      if (total >= this.cfg.prebufferMs) return false;
    }
    return total < this.cfg.prebufferMs;
  }

  /** Should the smoother insert a gap between this and the previous request? */
  shouldInsertGap(nextItem) {
    if (!this.cfg.useGap || this.cfg.gapMs <= 0) return false;
    if (this._lastPlayedAt == null) return false;
    if (!nextItem || !this._lastRequestId) return false;
    if (this._lastRequestId === nextItem.requestId) return false;
    return (this._now() - this._lastPlayedAt) < this.cfg.gapMs * 10;
  }

  /** Schedule the next chunk to play after the configured delay (if any). */
  schedulePlayback(playCallback) {
    const delay = this.shouldInsertGap(/* next */ null) ? this.cfg.gapMs : 0;
    this._schedule(delay, playCallback);
  }

  /** Mark that playback just started for this request id. */
  markPlayed(requestId) {
    this._lastPlayedAt = this._now();
    this._lastRequestId = requestId;
  }

  /** Reset smoothing state (e.g. on interrupt). */
  reset() {
    this._lastPlayedAt = null;
    this._lastRequestId = null;
  }
}
