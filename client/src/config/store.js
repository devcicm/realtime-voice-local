// ConfigStore — persisted runtime configuration.
//
// The user can change mode (local-chain / openai-realtime), transport
// (websocket / webrtc), LLM provider, TTS backend, STT model, sync
// mode, and voice. All choices are persisted to localStorage so the
// next session resumes with the same setup.
//
// The API key (when used) is stored in localStorage *only* if the
// user explicitly opts in via the "remember API key" toggle. The
// default is in-memory only.

const STORAGE_KEY = 'kokoro-tts-config';

const DEFAULTS = {
  mode: 'local-chain',                    // local-chain | openai-realtime | multimodal-local
  transport: 'websocket',                 // websocket | webrtc
  sttUrl: 'http://localhost:8100',
  llmUrl: 'http://localhost:12344/v1',
  ttsWsUrl: 'ws://localhost:8990/ws/tts',
  llmProvider: 'lmstudio',                // lmstudio | ollama | openai | anthropic
  llmModel: 'google/gemma-4-e2b',
  ttsBackend: 'piper',                    // piper | xtts | kokoro
  voice: 'es_ES/davefx-medium',
  sttModel: 'parakeet',                   // parakeet = el mas preciso y rapido en CPU
  syncMode: 'balanced',                   // free | balanced | strict | burst
  syncThresholdMs: 1500,
  systemPrompt: 'Eres un asistente amigable. Responde en espanol de forma natural y concisa.',
  maxTokens: 400,
  rememberApiKey: false,
  openaiApiKey: null,                     // in-memory only by default
  // Conversation layer (Phase 11)
  conversationWindowSize: 20,            // sliding window
  cancelMode: 'note',                    // silent | note | summary
  overflowStrategy: 'truncate',          // truncate | summarize
  sttDebounceMs: 1500,                   // STT streaming debounce
  cancelOnInterrupt: false,              // false = mark_interrupted (keep context, default)
                                         // true = cancel_finalized (privacy mode)
};

let _state = null;
const _listeners = new Set();

function _load() {
  if (_state) return _state;
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      _state = { ...DEFAULTS, ...parsed };
      if (!_state.rememberApiKey) _state.openaiApiKey = null;
    } else {
      _state = { ...DEFAULTS };
    }
  } catch (_) {
    _state = { ...DEFAULTS };
  }
  return _state;
}

function _save() {
  if (!_state) return;
  const toSave = { ..._state };
  if (!toSave.rememberApiKey) delete toSave.openaiApiKey;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
  } catch (_) { /* ignore quota errors */ }
}

function _emit() {
  for (const cb of _listeners) {
    try { cb(_state); } catch (_) { /* ignore */ }
  }
}

export const ConfigStore = {
  get() { return _load(); },

  set(patch) {
    _load();
    _state = { ..._state, ...patch };
    _save();
    _emit();
    return _state;
  },

  setApiKey(apiKey) {
    _load();
    _state = { ..._state, openaiApiKey: apiKey };
    _save();  // _save() strips the key unless rememberApiKey is on
    _emit();
  },

  reset() {
    _state = { ...DEFAULTS };
    _save();
    _emit();
  },

  onChange(cb) {
    _listeners.add(cb);
    return () => _listeners.delete(cb);
  },

  DEFAULTS,
};
