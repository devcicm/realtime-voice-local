// TurnController — single authority for cancel/interrupt of the active Run.
//
// Owns the active RunContext and exposes:
//   - newRun(turnId): create a new run, returns it (also sets as active)
//   - interrupt(reason, currentPlayedMs): atomic cancel end-to-end
//   - activeRun: getter for the current run
//   - runHistory: list of last N runs for inspection
//
// invariant: at most ONE active run at any time. interrupt() is idempotent.

import { RunContext, RunState } from './runContext.js';

const HISTORY_SIZE = 20;

export class TurnController {
  constructor({ bus, conversationLog, onInterrupt, getActiveWs } = {}) {
    this._bus = bus;                       // FencedBus (P0.5)
    this._log = conversationLog;
    this._onInterrupt = onInterrupt;       // callback (run, reason) => void
    this._getActiveWs = getActiveWs;       // () => WebSocket | null
    this._activeRun = null;
    this._lastInterruptedRun = null;       // for idempotent interrupt()
    this._runHistory = [];                 // FIFO
    this._listeners = new Set();
  }

  /** Currently active RunContext, or null. */
  get activeRun() { return this._activeRun; }
  get activeRunId() { return this._activeRun?.runId ?? null; }

  /** Read-only snapshot of past runs (most recent first). */
  get history() { return [...this._runHistory].reverse(); }

  /** Subscribe to lifecycle events. */
  on(cb) { this._listeners.add(cb); return () => this._listeners.delete(cb); }
  _emit(event, payload) {
    for (const cb of this._listeners) {
      try { cb({ event, ...payload }); } catch (_) { /* ignore */ }
    }
  }

  /** Create a new run and mark it active. Returns the new RunContext. */
  newRun({ sessionId, conversationId, turnId, systemPrompt, speed } = {}) {
    // If there's an active run that's not terminal, finalize it as
    // interrupted first (defensive — should not happen if interrupt is
    // called before newRun).
    if (this._activeRun && !this._activeRun.isTerminal()) {
      this._activeRun.transition(RunState.INTERRUPTED);
      this._archiveRun(this._activeRun);
    }
    this._lastInterruptedRun = null;   // reset idempotency
    const run = new RunContext({ sessionId, conversationId, turnId, systemPrompt, speed });
    this._activeRun = run;
    this._emit('run.created', { run });
    return run;
  }

  /** Atomic end-to-end interrupt of the active run.
   *
   *  Idempotent: calling on an already-interrupted run is a no-op.
   *  Returns true if a run was interrupted (or was already interrupted),
   *  false if there is no active run.
   *
   *  Effects (in order):
   *   1. transitions run state to INTERRUPTING (if not already)
   *   2. aborts the underlying fetch (LLM cancel)
   *   3. calls onInterrupt callback (TTS WS cancel + audio stop)
   *   4. truncates the conversation log using word_offsets_ms
   *   5. marks the run as INTERRUPTED and archives it
   */
  async interrupt(reason = 'barge-in', currentPlayedMs = null) {
    // Idempotency: if the last action was an interrupt, return true
    // without doing anything else. This handles the case where multiple
    // sources (VAD, stop button, user input) all fire the interrupt
    // around the same time.
    if (this._lastInterruptedRun) return true;

    const run = this._activeRun;
    if (!run) return false;
    if (run.state === RunState.INTERRUPTED) return true;   // idempotent
    if (run.state === RunState.COMPLETED) return true;       // can't cancel a done run
    if (run.state === RunState.FAILED) return true;

    const t0 = performance.now();

    // 1. transition to INTERRUPTING
    run.transition(RunState.INTERRUPTING);

    // 2. abort the LLM fetch
    run.abort(reason);

    // 3. notify the TTS / audio layer (callback)
    if (this._onInterrupt) {
      try {
        const result = this._onInterrupt(run, reason);
        if (result && typeof result.then === 'function') {
          await result;
        }
      } catch (e) {
        // Don't let callback errors break the cancel chain.
        console.warn('[TurnController] onInterrupt callback threw:', e);
      }
    }

    // 4. truncate the conversation log
    if (this._log && typeof this._log.truncate === 'function') {
      try {
        this._log.truncate(run.turnId, currentPlayedMs ?? run.playedAudioMs);
      } catch (e) {
        console.warn('[TurnController] truncate failed:', e);
      }
    }
    if (this._log && typeof this._log.mark_interrupted === 'function') {
      try {
        this._log.mark_interrupted('assistant', reason, run.spokenText || run.generatedText);
      } catch (e) {
        console.warn('[TurnController] mark_interrupted failed:', e);
      }
    }

    // 5. finalize
    run.transition(RunState.INTERRUPTED);
    this._archiveRun(run);
    this._activeRun = null;
    this._lastInterruptedRun = run;   // remember for idempotency

    const elapsed = performance.now() - t0;
    this._emit('run.interrupted', { run, reason, elapsedMs: elapsed });
    return true;
  }

  /** Mark the active run as completed (success). */
  complete() {
    const run = this._activeRun;
    if (!run) return;
    if (run.isTerminal()) return;
    run.transition(RunState.COMPLETED);
    this._archiveRun(run);
    this._activeRun = null;
    this._lastInterruptedRun = null;
    this._emit('run.completed', { run });
  }

  /** Mark the active run as failed. */
  fail(error) {
    const run = this._activeRun;
    if (!run) return;
    if (run.isTerminal()) return;
    run.transition(RunState.FAILED);
    this._archiveRun(run);
    this._activeRun = null;
    this._lastInterruptedRun = null;
    this._emit('run.failed', { run, error });
  }

  _archiveRun(run) {
    this._runHistory.push(run);
    if (this._runHistory.length > HISTORY_SIZE) {
      this._runHistory.shift();
    }
  }
}
