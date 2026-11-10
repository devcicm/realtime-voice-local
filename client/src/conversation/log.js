// Client mirror of server/shared/conversation/log.py.
// Same API surface, same invariants. See the Python docstring for details.

const CANCEL_MODES = ['silent', 'note', 'summary'];

function _newId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, '');
  return Array.from({ length: 16 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
}

function _truncateWords(text, maxChars) {
  if (!text || text.length <= maxChars) return text;
  const cut = text.slice(0, maxChars);
  const lastSpace = cut.lastIndexOf(' ');
  return (lastSpace > 0 ? cut.slice(0, lastSpace) : cut) + '...';
}

function _firstSentence(text) {
  if (!text) return '';
  const m = text.match(/[.!?]/);
  if (!m) return text;
  return text.slice(0, m.index + 1).trim();
}

function makeSilent() { return null; }
function makeNote(role, reason, partial) {
  const preview = _truncateWords(partial || '', 80);
  return {
    id: _newId(),
    role: 'system',
    content: `[Previous ${role} response was interrupted by the user (reason: ${reason}). Partial content: "${preview}"]`,
    status: 'finalized',
    source: 'synthetic',
  };
}
function makeSummary(role, reason, partial) {
  const summary = _truncateWords(_firstSentence(partial || ''), 120);
  return {
    id: _newId(),
    role: 'system',
    content: `[Previous ${role} response was interrupted by the user (reason: ${reason}). Summary: "${summary}"]`,
    status: 'finalized',
    source: 'synthetic',
  };
}

const APPLIERS = { silent: makeSilent, note: makeNote, summary: makeSummary };

function applyCancelMode(mode, role, reason, partial) {
  if (!APPLIERS[mode]) return null;
  return APPLIERS[mode](role, reason, partial || '');
}

export class Turn {
  constructor({ id, role, content, status = 'finalized', source = 'text', reasoning = null, ...rest }) {
    this.id = id || _newId();
    this.role = role;                          // 'user' | 'assistant' | 'system'
    this.content = content;
    this.status = status;                      // 'streaming' | 'finalized' | 'interrupted' | 'canceled'
    this.source = source;                      // for user: 'text' | 'stt'
    this.reasoning = reasoning;
    this.createdAtMs = rest.createdAtMs ?? performance.now();
    this.finalizedAtMs = rest.finalizedAtMs ?? (status === 'finalized' ? performance.now() : null);
    this.canceledAtMs = rest.canceledAtMs ?? null;
    this.interruptedAtMs = rest.interruptedAtMs ?? null;
    this.cancelReason = rest.cancelReason ?? null;
    this.interruptedReason = rest.interruptedReason ?? null;
    this.partialAtCancel = rest.partialAtCancel ?? null;
    this.partialAtInterrupt = rest.partialAtInterrupt ?? null;
    this.truncatedAtMs = rest.truncatedAtMs ?? null;
  }
  toDict() {
    return {
      id: this.id, role: this.role, content: this.content, status: this.status,
      source: this.source, reasoning: this.reasoning,
      createdAtMs: this.createdAtMs, finalizedAtMs: this.finalizedAtMs,
      canceledAtMs: this.canceledAtMs, interruptedAtMs: this.interruptedAtMs,
      cancelReason: this.cancelReason, interruptedReason: this.interruptedReason,
      partialAtCancel: this.partialAtCancel, partialAtInterrupt: this.partialAtInterrupt,
      truncatedAtMs: this.truncatedAtMs,
    };
  }
}

export class ConversationLog {
  constructor({ windowSize = 20, cancelMode = 'note', overflowStrategy = 'truncate', summarizer = null } = {}) {
    this._turns = new Map();                  // id -> Turn
    this._synthetic = [];                     // system turns produced by cancel/summary
    this._insertionOrder = [];               // for FIFO eviction
    this._listeners = { turnAdded: [], turnCanceled: [], turnInterrupted: [], turnTruncated: [], slid: [], cleared: [] };
    this.windowSize = windowSize;
    this.cancelMode = cancelMode;
    this.overflowStrategy = overflowStrategy;
    this._summarizer = summarizer;
    this._idCounter = 0;
  }

  _newId() {
    this._idCounter += 1;
    return `t-${Date.now().toString(36)}-${this._idCounter}`;
  }

  // ---- subscribe ----
  on(event, cb) {
    if (!this._listeners[event]) throw new Error(`Unknown event: ${event}`);
    this._listeners[event].push(cb);
    return () => {
      const arr = this._listeners[event];
      const i = arr.indexOf(cb);
      if (i >= 0) arr.splice(i, 1);
    };
  }
  _emit(event, payload) {
    for (const cb of this._listeners[event] || []) {
      try { cb({ event, ...payload }); } catch (_) { /* ignore */ }
    }
  }

  // ---- writes ----
  addUserTurn(text, source = 'text') {
    const t = new Turn({ role: 'user', content: text, source, status: 'finalized' });
    this._turns.set(t.id, t);
    this._insertionOrder.push(t.id);
    this._enforceWindow();
    this._emit('turnAdded', { turn: t.toDict(), role: 'user' });
    return t;
  }

  addAssistantTurn(text, reasoning = null) {
    const t = new Turn({ role: 'assistant', content: text, reasoning, status: 'finalized' });
    this._turns.set(t.id, t);
    this._insertionOrder.push(t.id);
    this._enforceWindow();
    this._emit('turnAdded', { turn: t.toDict(), role: 'assistant' });
    return t;
  }

  streamUserPartial(text) {
    let existing;
    for (const t of this._turns.values()) {
      if (t.role === 'user' && t.status === 'streaming') { existing = t; break; }
    }
    if (!existing) {
      const t = new Turn({ role: 'user', content: text, source: 'stt', status: 'streaming' });
      this._turns.set(t.id, t);
      this._insertionOrder.push(t.id);
      return t;
    }
    existing.content = text;
    return existing;
  }

  commitUserTurn() {
    for (const t of this._turns.values()) {
      if (t.role === 'user' && t.status === 'streaming') {
        t.status = 'finalized';
        t.finalizedAtMs = performance.now();
        this._enforceWindow();
        this._emit('turnAdded', { turn: t.toDict(), role: 'user', finalized: true });
        return t;
      }
    }
    return null;
  }

  cancelStream(role) {
    for (const [id, t] of this._turns) {
      if (t.role === role && t.status === 'streaming') {
        this._turns.delete(id);
        const idx = this._insertionOrder.indexOf(id);
        if (idx >= 0) this._insertionOrder.splice(idx, 1);
        return;
      }
    }
  }

  cancelFinalized(role, reason = 'barge-in', summaryText = null) {
    let target = null;
    for (const t of this._turns.values()) {
      if (t.role === role && t.status === 'finalized') target = t;
    }
    if (!target) return null;
    target.status = 'canceled';
    target.canceledAtMs = performance.now();
    target.cancelReason = reason;
    target.partialAtCancel = summaryText || target.content;

    const sys = applyCancelMode(this.cancelMode, role, reason, target.content);
    if (sys) {
      this._synthetic.push(sys);
      this._emit('turnAdded', { turn: sys, role: 'system', synthetic: true });
    }
    this._emit('turnCanceled', { turn: target.toDict(), reason, mode: this.cancelMode });
    return target;
  }

  /**
   * Mark the latest finalized/interrupted turn as interrupted by barge-in.
   * Unlike `cancelFinalized`, this KEEPS the turn in the conversation
   * context (fluid communication). The LLM sees the partial response.
   */
  markInterrupted(role, reason = 'barge-in', partialText = null) {
    let target = null;
    for (const t of this._turns.values()) {
      if (t.role === role && (t.status === 'finalized' || t.status === 'interrupted')) {
        target = t;
      }
    }
    if (!target) return null;
    target.status = 'interrupted';
    target.interruptedAtMs = performance.now();
    target.interruptedReason = reason;
    if (partialText !== null) target.partialAtInterrupt = partialText;
    this._emit('turnInterrupted', { turn: target.toDict(), reason });
    return target;
  }

  /**
   * Truncate the most recent assistant turn to the audio actually played.
   * If wordOffsetsMs is missing, the turn is left unchanged (conservative).
   * Idempotent.
   */
  truncate(turnId, playedAudioMs, wordOffsetsMs = null) {
    let turn = this._turns.get(turnId);
    if (!turn) {
      const candidates = [...this._turns.values()].filter(t =>
        t.role === 'assistant' && (t.status === 'finalized' || t.status === 'interrupted')
      );
      if (candidates.length === 0) return null;
      turn = candidates[candidates.length - 1];
    }
    if (turn.truncatedAtMs != null) return turn;
    if (!Array.isArray(wordOffsetsMs) || wordOffsetsMs.length === 0) return turn;
    if (typeof playedAudioMs !== 'number' || playedAudioMs <= 0) return turn;

    let cutoff = 0;
    for (let i = 0; i < wordOffsetsMs.length; i++) {
      if (wordOffsetsMs[i] <= playedAudioMs) cutoff = i + 1;
      else break;
    }
    if (cutoff === 0) {
      turn.content = '';
    } else {
      const words = turn.content.split(/\s+/);
      const kept = words.slice(0, Math.min(cutoff, words.length));
      turn.content = kept.join(' ');
    }
    turn.truncatedAtMs = playedAudioMs;
    this._emit('turnTruncated', { turn: turn.toDict(), playedAudioMs });
    return turn;
  }

  clear() {
    this._turns.clear();
    this._synthetic = [];
    this._insertionOrder = [];
    this._emit('cleared', {});
  }

  // ---- reads ----
  toMessages(systemPrompt = null, cancelMode = null) {
    const mode = cancelMode || this.cancelMode;
    let sequence = [];
    for (const t of this._turns.values()) {
      if (t.status === 'streaming') continue;
      if (t.status === 'canceled') continue;
      // 'finalized' AND 'interrupted' both stay in context (interrupt
      // preserves the conversation history for fluid multi-turn).
      sequence.push(t);
    }
    sequence = sequence.concat(this._synthetic);
    if (sequence.length > this.windowSize) {
      sequence = sequence.slice(-this.windowSize);
    }
    const msgs = [];
    if (systemPrompt) msgs.push({ role: 'system', content: systemPrompt });
    for (const t of sequence) msgs.push(this._turnToMessage(t));
    return msgs;
  }

  toRealtimeEvents() {
    const events = [];
    for (const t of this._turns.values()) {
      if (t.status === 'finalized') events.push(this._turnToRealtimeEvent(t, 'create'));
    }
    for (const t of this._synthetic) events.push(this._turnToRealtimeEvent(t, 'create'));
    return events;
  }

  history(limit = null) {
    const turns = [...this._turns.values()];
    const out = limit ? turns.slice(-limit) : turns;
    return [...out.map(t => t.toDict()), ...this._synthetic.map(t => t.toDict())];
  }

  snapshot() {
    return {
      turnCount: this._turns.size,
      syntheticCount: this._synthetic.length,
      windowSize: this.windowSize,
      cancelMode: this.cancelMode,
      overflowStrategy: this.overflowStrategy,
      turns: [...this._turns.values()].map(t => t.toDict()),
      synthetic: this._synthetic.map(t => t.toDict()),
    };
  }

  count() { return this._turns.size; }

  // ---- config ----
  setWindowSize(n) {
    this.windowSize = Math.max(1, n | 0);
    while ((this._turns.size + this._synthetic.length) > this.windowSize) {
      this._enforceWindow();
      if ((this._turns.size + this._synthetic.length) <= this.windowSize) break;
    }
    this._emit('slid', { windowSize: this.windowSize });
  }

  setCancelMode(mode) {
    if (!CANCEL_MODES.includes(mode)) throw new Error(`Unknown cancel mode: ${mode}`);
    this.cancelMode = mode;
  }

  setOverflowStrategy(s) {
    if (!['truncate', 'summarize'].includes(s)) throw new Error(`Unknown strategy: ${s}`);
    this.overflowStrategy = s;
  }

  setSummarizer(fn) { this._summarizer = fn; }

  // ---- internals ----
  _enforceWindow() {
    while (true) {
      const total = this._turns.size + this._synthetic.length;
      if (total <= this.windowSize) return;
      if (this.overflowStrategy === 'truncate') {
        if (this._turns.size > 0) {
          const oldest = this._insertionOrder.shift();
          this._turns.delete(oldest);
        } else if (this._synthetic.length > 0) {
          this._synthetic.shift();
        } else return;
        continue;
      }
      // summarize
      if (!this._summarizer) {
        if (this._turns.size > 0) {
          const oldest = this._insertionOrder.shift();
          this._turns.delete(oldest);
        } else if (this._synthetic.length > 0) {
          this._synthetic.shift();
        } else return;
        continue;
      }
      const evicted = [];
      while (this._turns.size > 0 && (this._turns.size + this._synthetic.length) > this.windowSize - 1) {
        const oldest = this._insertionOrder.shift();
        const t = this._turns.get(oldest);
        if (t) {
          this._turns.delete(oldest);
          evicted.push(t);
        } else break;
      }
      if (evicted.length === 0) {
        if (this._synthetic.length > 0) this._synthetic.shift();
        continue;
      }
      let summary;
      try { summary = this._summarizer(evicted); }
      catch (_) { summary = `[Summary of ${evicted.length} earlier turns failed]`; }
      this._synthetic.unshift({
        id: this._newId(),
        role: 'system',
        content: summary,
        status: 'finalized',
        source: 'synthetic',
      });
    }
  }

  _turnToMessage(t) {
    if (t.role === 'user') return { role: 'user', content: t.content };
    if (t.role === 'assistant') {
      if (t.reasoning && !t.content.includes(t.reasoning)) {
        return { role: 'assistant', content: `[reasoning]\n${t.reasoning}\n[/reasoning]\n${t.content}` };
      }
      return { role: 'assistant', content: t.content };
    }
    return { role: 'system', content: t.content };
  }

  _turnToRealtimeEvent(t, event) {
    if (event === 'delete') return { type: 'conversation.item.delete', item_id: t.id };
    const item = { id: t.id, type: 'message', role: t.role };
    if (t.role === 'user') item.content = [{ type: 'input_text', text: t.content }];
    else if (t.role === 'assistant') item.content = [{ type: 'text', text: t.content }];
    else item.content = [{ type: 'input_text', text: t.content }];
    return { type: 'conversation.item.create', item };
  }
}

export { CANCEL_MODES, applyCancelMode, makeSilent, makeNote, makeSummary };
