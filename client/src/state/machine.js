// Hierarchical State Machine — base class.
import { EventType } from '../events/types.js';

export class InvalidTransitionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'InvalidTransitionError';
  }
}

let _now = () => performance.now();

export class StateMachine {
  constructor({ name, states, initial, observers = [] } = {}) {
    if (!states) throw new Error('StateMachine requires STATES');
    if (!initial) throw new Error('StateMachine requires INITIAL');
    this.name = name || this.constructor.name;
    this.STATES = states;
    this.INITIAL = initial;
    this.current = initial;
    this.history = [{
      from: '',
      to: initial,
      reason: 'system-default',
      at_ms: _now(),
    }];
    this._observers = new Set(observers);
  }

  canTransition(to) {
    const allowed = this._transitionsOf(this.current);
    return allowed.has(to);
  }

  /**
   * Return the Set of valid next states for the given state.
   * Supports two formats:
   *   { STATE: { transitions: Set } }   (legacy, object-of-objects)
   *   { STATE: Set }                    (compact, object-of-sets)
   */
  _transitionsOf(state) {
    const v = this.STATES[state];
    if (!v) return new Set();
    if (v instanceof Set) return v;
    return v.transitions || new Set();
  }

  transition(to, reason = '') {
    if (to === this.current) return this;
    if (!this.canTransition(to)) {
      const allowed = this._transitionsOf(this.current);
      throw new InvalidTransitionError(
        `${this.name}: cannot transition ${this.current} -> ${to}. ` +
        `Allowed from ${this.current}: ${[...allowed]}`
      );
    }
    const from = this.current;
    this.current = to;
    const rec = { from, to, reason, at_ms: _now() };
    this.history.push(rec);
    this._notify(rec);
    return this;
  }

  force(to, reason = 'force') {
    const from = this.current;
    this.current = to;
    const rec = { from, to, reason: '[force] ' + reason, at_ms: _now() };
    this.history.push(rec);
    this._notify(rec);
    return this;
  }

  isIn(...states) { return states.includes(this.current); }
  isTerminal() {
    return this._transitionsOf(this.current).size === 0;
  }

  snapshot() {
    return {
      name: this.name,
      current: this.current,
      history: [...this.history],
    };
  }

  on(observer) { this._observers.add(observer); return () => this._observers.delete(observer); }
  _notify(record) {
    for (const o of this._observers) {
      try { o(this, record); } catch (_) { /* ignore */ }
    }
  }
}

// Connection transport lifecycle.
export const ConnectionStates = {
  IDLE: 'IDLE',
  CONNECTING: 'CONNECTING',
  OPEN: 'OPEN',
  CLOSING: 'CLOSING',
  CLOSED: 'CLOSED',
  ERROR: 'ERROR',
};

const ConnectionTransitions = {
  IDLE:       new Set(['CONNECTING']),
  CONNECTING: new Set(['OPEN', 'CLOSED', 'ERROR']),
  OPEN:       new Set(['CLOSING', 'ERROR']),
  CLOSING:    new Set(['CLOSED', 'ERROR']),
  CLOSED:     new Set(['IDLE']),
  ERROR:      new Set(['IDLE']),
};

export class ConnectionStateMachine extends StateMachine {
  constructor() {
    super({
      name: 'Connection',
      states: ConnectionTransitions,
      initial: ConnectionStates.IDLE,
    });
  }
}

// User/model turn lifecycle.
export const TurnStates = {
  IDLE: 'IDLE',
  USER_SPEAKING: 'USER_SPEAKING',
  USER_DONE: 'USER_DONE',
  MODEL_THINKING: 'MODEL_THINKING',
  MODEL_SPEAKING: 'MODEL_SPEAKING',
  INTERRUPTED: 'INTERRUPTED',
};

const TurnTransitions = {
  IDLE:          new Set(['USER_SPEAKING']),
  USER_SPEAKING: new Set(['USER_DONE', 'INTERRUPTED']),
  USER_DONE:     new Set(['MODEL_THINKING', 'INTERRUPTED']),
  MODEL_THINKING: new Set(['MODEL_SPEAKING', 'INTERRUPTED']),
  MODEL_SPEAKING: new Set(['IDLE', 'INTERRUPTED']),
  INTERRUPTED:   new Set(['IDLE']),
};

export class TurnStateMachine extends StateMachine {
  constructor() {
    super({ name: 'Turn', states: TurnTransitions, initial: TurnStates.IDLE });
  }
}

// Chat pipeline state for one message.
export const PipelineStates = {
  READY: 'READY',
  STREAMING: 'STREAMING',
  FAILED: 'FAILED',
  INTERRUPTED: 'INTERRUPTED',
};

const PipelineTransitions = {
  READY: new Set(['STREAMING']),
  STREAMING: new Set(['READY', 'FAILED', 'INTERRUPTED']),
  FAILED: new Set(['READY']),
  INTERRUPTED: new Set(['READY']),
};

export class PipelineStateMachine extends StateMachine {
  constructor() {
    super({ name: 'Pipeline', states: PipelineTransitions, initial: PipelineStates.READY });
  }
}

// Per-message lifecycle.
export const MessageStates = {
  CREATED: 'CREATED',
  QUEUED: 'QUEUED',
  STT: 'STT',
  LLM: 'LLM',
  SPLIT: 'SPLIT',
  TTS: 'TTS',
  PLAYING: 'PLAYING',
  COMPLETED: 'COMPLETED',
  FAILED: 'FAILED',
  INTERRUPTED: 'INTERRUPTED',
};

const MessageTransitions = {
  CREATED:     new Set(['QUEUED', 'FAILED', 'INTERRUPTED']),
  QUEUED:      new Set(['STT', 'LLM', 'COMPLETED', 'FAILED', 'INTERRUPTED']),
  STT:         new Set(['LLM', 'COMPLETED', 'FAILED', 'INTERRUPTED']),
  LLM:         new Set(['SPLIT', 'COMPLETED', 'FAILED', 'INTERRUPTED']),
  SPLIT:       new Set(['TTS', 'COMPLETED', 'FAILED', 'INTERRUPTED']),
  TTS:         new Set(['PLAYING', 'COMPLETED', 'FAILED', 'INTERRUPTED']),
  PLAYING:     new Set(['COMPLETED', 'FAILED', 'INTERRUPTED']),
  COMPLETED:   new Set(),
  FAILED:      new Set(),
  INTERRUPTED: new Set(),
};

export class MessageStateMachine extends StateMachine {
  constructor() {
    super({ name: 'Message', states: MessageTransitions, initial: MessageStates.CREATED });
  }
}

// ---- Conversation-level FSM (the semantic lifecycle of a turn) ----

export const ConversationStates = {
  IDLE: 'IDLE',
  STREAMING: 'STREAMING',
  DELIVERED: 'DELIVERED',
  COMMITTED: 'COMMITTED',
  CANCELED: 'CANCELED',
};

const ConversationTransitions = {
  IDLE:      new Set(['STREAMING']),
  STREAMING: new Set(['DELIVERED', 'CANCELED']),
  DELIVERED: new Set(['COMMITTED', 'CANCELED']),
  COMMITTED: new Set(),
  CANCELED:  new Set(['IDLE']),
};

export class ConversationStateMachine extends StateMachine {
  constructor() {
    super({ name: 'Conversation', states: ConversationTransitions, initial: ConversationStates.IDLE });
  }
}
