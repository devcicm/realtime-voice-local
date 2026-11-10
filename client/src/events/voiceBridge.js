// VoiceBridge — joins voice-channel events to chat-channel events.
// Mirrors server/shared/events/voice_bridge.py.

export class VoiceBridge {
  constructor() {
    this._byChunk = new Map();   // chunkId -> {text, voiceId, traceId, sentAtMs}
    this._byTrace = new Map();   // traceId -> [chunkId]
  }

  bindChunk(chunkId, { text, voiceId, traceId = null }) {
    this._byChunk.set(chunkId, { text, voiceId, traceId, sentAtMs: performance.now() });
    if (traceId) {
      if (!this._byTrace.has(traceId)) this._byTrace.set(traceId, []);
      this._byTrace.get(traceId).push(chunkId);
    }
  }

  get(chunkId) { return this._byChunk.get(chunkId); }
  textOf(chunkId) { return this._byChunk.get(chunkId)?.text ?? null; }
  chunksForTrace(traceId) { return [...(this._byTrace.get(traceId) || [])]; }
  allChunks() { return [...this._byChunk.entries()].map(([cid, data]) => ({ chunkId: cid, ...data })); }
  clear() { this._byChunk.clear(); this._byTrace.clear(); }
}

let _singleton = null;
export function getVoiceBridge() {
  if (!_singleton) _singleton = new VoiceBridge();
  return _singleton;
}
