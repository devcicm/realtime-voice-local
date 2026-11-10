// SessionState — reactive snapshot of the realtime session.
// Mirrors server/shared/session/state.py.

let _state = null;
const _observers = new Set();

function _makeDefault() {
  return {
    session_id: '',
    connection: 'idle',          // idle | connecting | open | closing | closed | error
    transport: 'websocket',      // websocket | webrtc | http-stream
    backend: '',                 // piper | xtts | kokoro | openai-realtime
    turn: 'idle',                // idle | user_speaking | user_done | model_thinking | model_speaking | interrupted
    pipeline: 'ready',           // ready | streaming | failed | interrupted
    llm_provider: 'lmstudio',    // lmstudio | ollama | openai | anthropic
    llm_model: '',
    voice_id: '',
    capabilities: {},
    rate_limits: {},
    audio: {
      direction: 'none',
      sample_rate: 24000,
      queue_size: 0,
      queue_bytes: 0,
    },
    counters: {},
  };
}

function getState() {
  if (!_state) _state = _makeDefault();
  return _state;
}

function update(patch) {
  const s = getState();
  Object.assign(s, patch);
  for (const obs of _observers) {
    try { obs(s); } catch (_) { /* ignore */ }
  }
  return s;
}

function snapshot() { return { ...getState() }; }

function reset() { _state = _makeDefault(); }

function onChange(cb) { _observers.add(cb); return () => _observers.delete(cb); }

export const SessionState = {
  get: getState,
  update,
  snapshot,
  reset,
  onChange,
};
