// RealtimeTransport — abstract interface for the audio path.
//
// Three concrete implementations:
//   - LocalWSTransport:   STT (HTTP) + LLM (HTTP) + TTS (WebSocket). Default.
//   - LocalRTCTransport:  same, but TTS over WebRTC. Optional.
//   - OpenAIRealtimeTransport: WebRTC to gpt-realtime, single model.
//
// All three expose the same callbacks so the UI doesn't know which is
// in use. The router (config.js) picks one and rebuilds the UI when
// the user changes modes.

export class RealtimeTransport {
  /**
   * @param {object} config
   * @param {string} config.mode         "local-chain" | "openai-realtime" | "multimodal-local"
   * @param {string} config.transport    "websocket" | "webrtc"
   * @param {string} config.sttUrl
   * @param {string} config.llmUrl
   * @param {string} config.ttsUrl
   * @param {string} config.voice
   * @param {string} config.model
   * @param {string} config.apiKey       (only for openai-realtime)
   */
  constructor(config) {
    this.config = config;
    this._callbacks = {
      audioChunk: [],
      transcriptDelta: [],
      speechStarted: [],
      speechStopped: [],
      turnEnd: [],
      assistantDelta: [],
    };
    this.state = 'idle';   // mirrors ConnectionStateMachine
  }

  async connect() { throw new Error('abstract'); }
  async sendAudioChunk(_pcm32) { throw new Error('abstract'); }
  async sendText(_text) { throw new Error('abstract'); }
  interrupt() { throw new Error('abstract'); }
  updateSession(_patch) { throw new Error('abstract'); }
  close() { throw new Error('abstract'); }

  // ---- subscribe ----
  on(event, cb) {
    if (!this._callbacks[event]) throw new Error(`Unknown event: ${event}`);
    this._callbacks[event].push(cb);
    return () => {
      this._callbacks[event] = this._callbacks[event].filter(c => c !== cb);
    };
  }
  _emit(event, payload) {
    for (const cb of this._callbacks[event] || []) {
      try { cb(payload); } catch (_) { /* ignore */ }
    }
  }
}
