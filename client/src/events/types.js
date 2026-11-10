// Event catalog — must match server/shared/events/types.py and shared/events.json.
// Use EventType.CONN_OPEN, etc. — typos become undefined lookups.

export const EventType = {
  // Domain
  USER_INPUT_RECEIVED:   'user.input.received',
  STT_TRANSCRIBED:       'stt.transcribed',
  LLM_TOKEN_DELTA:       'llm.token.delta',
  LLM_RESPONSE_DONE:     'llm.response.done',
  TTS_CHUNK_SENT:        'tts.chunk.sent',
  TTS_AUDIO_DELTA:       'tts.audio.delta',
  TTS_AUDIO_END:         'tts.audio.end',
  AUDIO_PLAYED:          'audio.played',
  TRANSCRIPT_DELTA:      'transcript.delta',
  MESSAGE_DISPLAYED:     'message.displayed',

  // Communication
  CONN_OPENING:          'conn.opening',
  CONN_OPEN:             'conn.open',
  CONN_CLOSING:          'conn.closing',
  CONN_CLOSED:           'conn.closed',
  CONN_FAILED:           'conn.failed',
  CONN_RECONNECTING:     'conn.reconnecting',
  CONN_RECONNECTED:      'conn.reconnected',

  // Voice activity
  VAD_SPEECH_START:      'vad.speech.start',
  VAD_SPEECH_END:        'vad.speech.end',
  VAD_TURN_COMPLETE:     'vad.turn.complete',

  // Control
  INTERRUPT_REQUESTED:   'interrupt.requested',
  INTERRUPT_ACK:         'interrupt.ack',
  MODE_CHANGED:          'mode.changed',
  CONFIG_UPDATED:        'config.updated',

  // Diagnostic
  ERROR_RAISED:          'error.raised',
  LATENCY_OBSERVED:      'latency.observed',
  CIRCUIT_BREAKER_CHANGED: 'circuit_breaker.changed',
  STATE_CHANGED:         'state.changed',
};

export const EVENT_TYPES = Object.fromEntries(
  Object.entries(EventType).map(([k, v]) => [k, v])
);

export function isKnown(value) {
  return Object.values(EventType).includes(value);
}
