// Client mirror of server/shared/session/context.py.

export class SessionContext {
  constructor() {
    this._vadActive = false;
    this._userSpeaking = false;
    this._turn = 'idle';
    this._transport = 'websocket';
    this._audioQueueSize = 0;
    this._audioQueueBytes = 0;
    this._audioLastChunkAt = null;
    this._sttPartial = '';
    this._lastInterruption = null;
    this._listeners = new Set();
  }

  setVadActive(v) { this._vadActive = !!v; this._emit(); }
  setUserSpeaking(v) { this._userSpeaking = !!v; this._emit(); }
  setTurn(turn) { this._turn = turn; this._emit(); }
  setTransport(transport) { this._transport = transport; this._emit(); }
  setAudioQueue(size, bytes, lastChunkAt = null) {
    this._audioQueueSize = size | 0;
    this._audioQueueBytes = bytes | 0;
    this._audioLastChunkAt = lastChunkAt;
    this._emit();
  }
  setSttPartial(text) { this._sttPartial = text || ''; this._emit(); }
  setInterruption(reason, partial = '') {
    this._lastInterruption = { reason, ts: performance.now(), partial: partial || '' };
    this._emit();
  }
  clearInterruption() { this._lastInterruption = null; this._emit(); }
  reset() {
    this._vadActive = false;
    this._userSpeaking = false;
    this._turn = 'idle';
    this._sttPartial = '';
    this._audioQueueSize = 0;
    this._audioQueueBytes = 0;
    this._lastInterruption = null;
    this._emit();
  }

  isEmpty() {
    return (
      !this._vadActive &&
      !this._userSpeaking &&
      this._turn === 'idle' &&
      !this._sttPartial &&
      this._audioQueueSize === 0 &&
      this._audioQueueBytes === 0 &&
      this._lastInterruption === null
    );
  }

  toSystemContext() {
    if (this.isEmpty()) return '';
    const lines = ['[Session context]'];
    if (this._vadActive) lines.push('- VAD: user is currently speaking');
    else if (this._userSpeaking) lines.push('- User is currently speaking (pre-VAD)');
    if (this._turn && this._turn !== 'idle') lines.push(`- Turn: ${this._turn}`);
    if (this._sttPartial) {
      const preview = this._sttPartial.slice(0, 60);
      const suffix = this._sttPartial.length > 60 ? '...' : '';
      lines.push(`- STT partial: "${preview}${suffix}"`);
    }
    if (this._audioQueueSize > 0) {
      lines.push(`- Audio queue: ${this._audioQueueSize} chunks, ${this._audioQueueBytes} bytes`);
    }
    if (this._lastInterruption) {
      const ageS = (performance.now() - this._lastInterruption.ts) / 1000;
      if (ageS < 60) {
        lines.push(`- Last interruption: ${ageS.toFixed(1)}s ago (${this._lastInterruption.reason})`);
      }
    }
    if (this._transport) lines.push(`- Transport: ${this._transport}`);
    lines.push('[/Session context]');
    return lines.join('\n');
  }

  toJson() {
    return {
      vadActive: this._vadActive,
      userSpeaking: this._userSpeaking,
      turn: this._turn,
      transport: this._transport,
      audioQueue: {
        size: this._audioQueueSize,
        bytes: this._audioQueueBytes,
        lastChunkAt: this._audioLastChunkAt,
      },
      sttPartial: this._sttPartial,
      lastInterruption: this._lastInterruption,
    };
  }

  onChange(cb) { this._listeners.add(cb); return () => this._listeners.delete(cb); }
  _emit() { for (const cb of this._listeners) { try { cb(this); } catch (_) {} } }
}

let _instance = null;
export function getSessionContext() {
  if (!_instance) _instance = new SessionContext();
  return _instance;
}
export function resetSessionContext() { _instance = null; }
