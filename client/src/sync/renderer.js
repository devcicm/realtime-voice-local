// SyncRenderer — word-level highlight that mirrors audio playback.
//
// Each assistant message is rendered as a series of <span class="word">
// elements. As audio plays, the renderer updates the dataset.status
// attribute on each span so the CSS can style it as pending / speaking /
// spoken / interrupted. This is the same UX as OpenAI's Advanced Voice
// Mode: the word currently being spoken is highlighted in real time.

const STATUS = {
  PENDING: 'pending',
  SPEAKING: 'speaking',
  SPOKEN: 'spoken',
  INTERRUPTED: 'interrupted',
};

export class SyncRenderer {
  /**
   * @param {HTMLElement} container  Where the message body is rendered.
   * @param {string}     text        The text to render word-by-word.
   */
  constructor(container, text) {
    this.container = container;
    this.text = text || '';
    this.words = this.text.split(/(\s+)/);  // keep whitespace as separate tokens
    this.spans = [];                          // <span> for each word
    this.audioStartedAtMs = null;            // performance.now() when first audio arrived
    this.wordOffsetsMs = [];                 // from audio_end v2
    this.totalDurationMs = 0;
    this._render();
  }

  _render() {
    this.container.innerHTML = '';
    this.spans = [];
    let wordIdx = 0;
    for (const token of this.words) {
      if (/^\s+$/.test(token)) {
        this.container.appendChild(document.createTextNode(token));
      } else {
        const span = document.createElement('span');
        span.className = 'word';
        span.dataset.idx = String(wordIdx);
        span.dataset.status = STATUS.PENDING;
        span.textContent = token;
        this.container.appendChild(span);
        this.spans.push(span);
        wordIdx++;
      }
    }
  }

  /** Called when the first audio chunk for this chunk_id arrives. */
  onAudioStart() {
    this.audioStartedAtMs = performance.now();
  }

  /** Called on every audio_chunk header. We use the duration_ms field. */
  onAudioChunk(_seq, durationMs) {
    if (this.audioStartedAtMs == null) this.onAudioStart();
    this.totalDurationMs = Math.max(
      this.totalDurationMs,
      performance.now() - this.audioStartedAtMs + durationMs,
    );
    this._updateHighlights();
  }

  /** Called on audio_end with the word_offsets_ms from the server. */
  onAudioEnd(payload) {
    this.wordOffsetsMs = payload.word_offsets_ms || [];
    this.totalDurationMs = payload.duration_ms || this.totalDurationMs;
    this._updateHighlights();
    // After the audio finishes, mark everything as spoken.
    for (const span of this.spans) span.dataset.status = STATUS.SPOKEN;
  }

  /** Highlight the word currently being spoken based on wall-clock time. */
  _updateHighlights() {
    if (this.audioStartedAtMs == null) return;
    const elapsed = performance.now() - this.audioStartedAtMs;
    if (!this.wordOffsetsMs.length) return;
    for (let i = 0; i < this.spans.length; i++) {
      const start = this.wordOffsetsMs[i] ?? 0;
      const end = this.wordOffsetsMs[i + 1] ?? Infinity;
      const span = this.spans[i];
      if (elapsed < start) span.dataset.status = STATUS.PENDING;
      else if (elapsed < end) span.dataset.status = STATUS.SPEAKING;
      else span.dataset.status = STATUS.SPOKEN;
    }
  }

  /** Mark all words after a given index as interrupted. */
  cancel(fromIdx = 0) {
    for (let i = fromIdx; i < this.spans.length; i++) {
      this.spans[i].dataset.status = STATUS.INTERRUPTED;
    }
  }

  /** Set the full text (re-render). Used when text arrives in pieces. */
  setText(text) {
    this.text = text || '';
    this.words = this.text.split(/(\s+)/);
    this._render();
  }
}

export { STATUS as WordStatus };
