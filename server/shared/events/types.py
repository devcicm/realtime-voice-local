"""Event catalogue — single source of truth.

The values match the keys in ``shared/events.json``. Imported by both
client and server code so a typo in a type name is a Python import
error rather than a silent drop.

Mirrors ``client/src/events/types.js``.
"""
from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    # Domain
    USER_INPUT_RECEIVED   = "user.input.received"
    STT_TRANSCRIBED       = "stt.transcribed"
    LLM_TOKEN_DELTA       = "llm.token.delta"
    LLM_RESPONSE_DONE     = "llm.response.done"
    TTS_CHUNK_SENT        = "tts.chunk.sent"
    TTS_AUDIO_DELTA       = "tts.audio.delta"
    TTS_AUDIO_END         = "tts.audio.end"
    AUDIO_PLAYED          = "audio.played"
    TRANSCRIPT_DELTA      = "transcript.delta"
    MESSAGE_DISPLAYED     = "message.displayed"

    # Communication
    CONN_OPENING          = "conn.opening"
    CONN_OPEN             = "conn.open"
    CONN_CLOSING          = "conn.closing"
    CONN_CLOSED           = "conn.closed"
    CONN_FAILED           = "conn.failed"
    CONN_RECONNECTING     = "conn.reconnecting"
    CONN_RECONNECTED      = "conn.reconnected"

    # Voice activity
    VAD_SPEECH_START      = "vad.speech.start"
    VAD_SPEECH_END        = "vad.speech.end"
    VAD_TURN_COMPLETE     = "vad.turn.complete"

    # Control
    INTERRUPT_REQUESTED   = "interrupt.requested"
    INTERRUPT_ACK         = "interrupt.ack"
    MODE_CHANGED          = "mode.changed"
    CONFIG_UPDATED        = "config.updated"

    # Diagnostic
    ERROR_RAISED          = "error.raised"
    LATENCY_OBSERVED      = "latency.observed"
    CIRCUIT_BREAKER_CHANGED = "circuit_breaker.changed"
    STATE_CHANGED         = "state.changed"


# Snapshot of the dyn-loaded catalogue as plain strings, useful for
# fallback when EventType is not desirable (e.g. JSON serialization).
EVENT_TYPES: dict[str, str] = {e.name: e.value for e in EventType}


def is_known(value: str) -> bool:
    return value in {e.value for e in EventType}
