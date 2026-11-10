// STT debouncer — buffers streaming STT partials, commits when silence.
//
// Behavior:
//   - Every partial that arrives within `debounceMs` of the previous
//     one REPLACES the buffered text (we always have the latest).
//   - If no new partial arrives for `debounceMs`, the buffer is
//     committed (callback fired with the final text) and cleared.
//   - If `cancel()` is called, the buffer is dropped without commit.
//
// Wires into SessionContext for the partial preview.

export class SttDebouncer {
  /**
   * @param {object} opts
   * @param {number} [opts.debounceMs=1500]    ms of silence to wait before commit
   * @param {function} [opts.onCommit]         (text) => void
   * @param {function} [opts.onPartial]        (text) => void
   * @param {function} [opts.onCancel]         () => void
   * @param {function} [opts.now]              () => number
   * @param {function} [opts.schedule]         (ms, fn) => timer handle
   * @param {function} [opts.clearTimer]       (handle) => void
   */
  constructor({
    debounceMs = 1500,
    onCommit = null,
    onPartial = null,
    onCancel = null,
    now = () => performance.now(),
    schedule = (ms, fn) => setTimeout(fn, ms),
    clearTimer = (h) => clearTimeout(h),
  } = {}) {
    this.debounceMs = debounceMs;
    this._onCommit = onCommit;
    this._onPartial = onPartial;
    this._onCancel = onCancel;
    this._now = now;
    this._schedule = schedule;
    this._clearTimer = clearTimer;
    this._buffer = '';
    this._timer = null;
  }

  /** A new partial arrived. Updates the buffer and resets the debounce timer. */
  pushPartial(text) {
    this._buffer = text || '';
    if (this._onPartial) {
      try { this._onPartial(this._buffer); } catch (_) {}
    }
    this._resetTimer();
  }

  /** Force-commit the buffer (e.g. when the user explicitly sends). */
  commit() {
    this._cancelTimer();
    const text = this._buffer;
    this._buffer = '';
    if (text && this._onCommit) {
      try { this._onCommit(text); } catch (_) {}
    }
    return text;
  }

  /** Discard the buffer. Fired when barge-in or abort. */
  cancel() {
    this._cancelTimer();
    this._buffer = '';
    if (this._onCancel) {
      try { this._onCancel(); } catch (_) {}
    }
  }

  get pending() { return this._buffer; }
  get isCommitting() { return this._timer !== null; }

  _resetTimer() {
    this._cancelTimer();
    this._timer = this._schedule(this.debounceMs, () => {
      this._timer = null;
      this.commit();
    });
  }

  _cancelTimer() {
    if (this._timer !== null) {
      this._clearTimer(this._timer);
      this._timer = null;
    }
  }
}
