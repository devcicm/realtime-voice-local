// LLMGatekeeper — backpressure on the LLM stream so text doesn't race ahead of audio.
//
// Tracks the difference between text displayed to the user and audio
// played. When text-ahead exceeds a threshold, the gatekeeper requests
// the upstream LLM stream to pause. When the audio catches up, the
// stream is resumed. This is the "balanced" mode from the original plan.
//
// The gatekeeper is a pure controller — it does not own the stream. The
// caller wires it up:

//   const gatekeeper = new LLMGatekeeper({ mode: 'balanced' });
//   gatekeeper.onPause = () => reader.pause();
//   gatekeeper.onResume = () => reader.resume();
//
//   while (true) {
//     const action = gatekeeper.tickBeforeChunk();
//     if (action === 'pause') await waitForResume();
//     const { value, done } = await reader.read();
//     if (done) break;
//     gatekeeper.onTextAdded(decodeChunk(value));
//   }

const MODES = {
  free:    { maxTextAheadMs: Infinity, maxAudioAheadMs: Infinity },
  balanced:{ maxTextAheadMs: 1500,     maxAudioAheadMs: 800 },
  strict:  { maxTextAheadMs: 400,      maxAudioAheadMs: 400 },
  burst:   { maxTextAheadMs: Infinity, maxAudioAheadMs: Infinity },
};

export class LLMGatekeeper {
  constructor({ mode = 'balanced', thresholdMs, resumeAtFraction = 0.5 } = {}) {
    const cfg = MODES[mode] || MODES.balanced;
    this.mode = mode;
    this.maxTextAheadMs = thresholdMs ?? cfg.maxTextAheadMs;
    this.maxAudioAheadMs = cfg.maxAudioAheadMs;
    this.resumeAtFraction = resumeAtFraction;  // resume when text-ahead drops to this fraction of the cap
    this.textAheadMs = 0;
    this.audioAheadMs = 0;
    this.paused = false;
    this.onPause = null;
    this.onResume = null;
  }

  /** ~350ms per Spanish word — coarse estimate, good enough for backpressure. */
  static estimateTextMs(text) {
    const words = (text || '').trim().split(/\s+/).filter(Boolean).length;
    return words * 350;
  }

  /** Estimate audio ms from sample count and rate. */
  static estimateAudioMs(samples, sampleRate) {
    if (!sampleRate) return 0;
    return (samples / sampleRate) * 1000;
  }

  onTextAdded(text) {
    const ms = LLMGatekeeper.estimateTextMs(text);
    this.textAheadMs += ms;
    return this._maybePause();
  }

  onAudioPlayed(durationMs) {
    this.audioAheadMs += durationMs;
    this.textAheadMs = Math.max(0, this.textAheadMs - durationMs);
    return this._maybeResume();
  }

  onAudioQueued(durationMs) {
    this.audioAheadMs += durationMs;
    return this._maybePause();
  }

  _maybePause() {
    if (this.paused) return null;
    if (this.textAheadMs <= this.maxTextAheadMs) return null;
    this.paused = true;
    if (this.onPause) {
      try { this.onPause(); } catch (_) { /* ignore */ }
    }
    return 'pause';
  }

  _maybeResume() {
    if (!this.paused) return null;
    const trigger = this.maxTextAheadMs * this.resumeAtFraction;
    if (this.textAheadMs > trigger) return null;
    this.paused = false;
    if (this.onResume) {
      try { this.onResume(); } catch (_) { /* ignore */ }
    }
    return 'resume';
  }

  /** Snapshot for metrics / inspection. */
  snapshot() {
    return {
      mode: this.mode,
      paused: this.paused,
      textAheadMs: Math.round(this.textAheadMs),
      audioAheadMs: Math.round(this.audioAheadMs),
      maxTextAheadMs: this.maxTextAheadMs,
      maxAudioAheadMs: this.maxAudioAheadMs,
    };
  }
}

export { MODES as GatekeeperModes };
