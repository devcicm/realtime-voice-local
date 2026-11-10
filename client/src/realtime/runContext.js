// RunContext — identity + state + abort handle for a single LLM/TTS run.
//
// A "Run" is a single end-to-end assistant turn attempt: from "user
// committed" to either "delivered" or "interrupted". Multiple Runs can
// exist over the lifetime of a session; only one is ACTIVE at a time.
// The RunContext owns:
//   - the AbortController that cancels the LLM fetch
//   - the run_id (uuid) for cross-component correlation
//   - snapshots of generated/spoken text (used by truncate on barge-in)
//   - state for the RunStateMachine (THINKING -> SPEAKING -> INTERRUPTING -> INTERRUPTED | COMPLETED)
//
// Guards:
//   - All state transitions are idempotent: repeating "INTERRUPTING" on
//     an already-interrupting run is a no-op.
//   - abort() can be called multiple times safely.
//   - snapshot fields are clamped: spokenText.length <= generatedText.length
//     (we never pretend the user heard more than was generated).

export const RunState = {
  CREATED:      'created',       // Run object exists; not yet dispatched
  THINKING:     'thinking',      // LLM fetch in progress
  SPEAKING:     'speaking',      // TTS producing audio; some audio may have been played
  INTERRUPTING: 'interrupting',  // cancellation in progress
  INTERRUPTED:  'interrupted',   // cancelled; stays in context by default
  COMPLETED:    'completed',     // delivered successfully
  FAILED:       'failed',        // unrecoverable error
};

const VALID_TRANSITIONS = {
  created:      ['thinking', 'failed', 'interrupting', 'interrupted'],
  thinking:     ['speaking', 'interrupted', 'interrupting', 'failed'],
  speaking:     ['completed', 'interrupted', 'interrupting', 'failed'],
  interrupting: ['interrupted', 'failed', 'created'],   // can resume to thinking if rollback
  interrupted:  ['created', 'failed'],                   // can start a new run
  completed:    ['interrupting'],                        // late cancel race
  failed:       ['interrupting'],                        // late cancel race
};

let _idCounter = 0;
function _newRunId() {
  _idCounter += 1;
  return `run-${Date.now().toString(36)}-${_idCounter}`;
}

export class RunContext {
  constructor({ sessionId, conversationId, turnId, systemPrompt = '', speed = 1.0 } = {}) {
    this.runId = _newRunId();
    this.sessionId = sessionId || 'sess-default';
    this.conversationId = conversationId || 'conv-default';
    this.turnId = turnId || 'turn-default';
    this.systemPrompt = systemPrompt;
    this.speed = speed;
    this.state = RunState.CREATED;
    this.abortController = new AbortController();
    this.createdAtMs = performance.now();
    this.lastTransitionAtMs = this.createdAtMs;
    // Snapshots for truncate.
    this.generatedText = '';
    this.spokenText = '';          // what the user actually heard
    this.generatedAudioMs = 0;
    this.playedAudioMs = 0;
    this._listeners = new Set();
  }

  /** Subscribe to state transitions. Returns an unsubscribe function. */
  onTransition(cb) {
    this._listeners.add(cb);
    return () => this._listeners.delete(cb);
  }

  /** Transition to a new state. Idempotent: same state = no-op.
   *  Invalid transitions throw (caught by TurnController for safety).
   */
  transition(next) {
    if (this.state === next) return this;
    const allowed = VALID_TRANSITIONS[this.state] || [];
    if (!allowed.includes(next)) {
      // Don't throw — return false so callers can log and continue.
      return false;
    }
    const from = this.state;
    this.state = next;
    this.lastTransitionAtMs = performance.now();
    for (const cb of this._listeners) {
      try { cb({ from, to: next, run: this, at: this.lastTransitionAtMs }); }
      catch (_) { /* ignore observer errors */ }
    }
    return this;
  }

  /** Snapshot the LLM-generated text (called on every LLM_TOKEN). */
  appendGenerated(text) {
    if (typeof text === 'string' && text) {
      this.generatedText += text;
    }
  }

  /** Snapshot the audio actually played. Called on every audio chunk. */
  appendPlayedAudio(durationMs) {
    if (typeof durationMs === 'number' && durationMs > 0) {
      this.playedAudioMs += durationMs;
    }
  }

  /** Snapshot the TTS-generated audio. */
  appendGeneratedAudio(durationMs) {
    if (typeof durationMs === 'number' && durationMs > 0) {
      this.generatedAudioMs += durationMs;
    }
  }

  /** Abort the underlying fetch. Safe to call multiple times. */
  abort(reason = 'barge-in') {
    try {
      this.abortController.abort(reason);
    } catch (_) { /* already aborted; no-op */ }
  }

  /** True if this run is in a terminal or interrupting state. */
  isTerminal() {
    return [RunState.INTERRUPTED, RunState.COMPLETED, RunState.FAILED].includes(this.state);
  }

  toJSON() {
    return {
      runId: this.runId,
      sessionId: this.sessionId,
      conversationId: this.conversationId,
      turnId: this.turnId,
      state: this.state,
      generatedTextLen: this.generatedText.length,
      spokenTextLen: this.spokenText.length,
      playedAudioMs: Math.round(this.playedAudioMs),
      createdAtMs: this.createdAtMs,
      lastTransitionAtMs: this.lastTransitionAtMs,
    };
  }
}
