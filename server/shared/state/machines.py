"""Connection FSM — the lifecycle of the realtime transport.

States:
  IDLE       -> CONNECTING (start)
  CONNECTING -> OPEN | CLOSED | ERROR
  OPEN       -> CLOSING | ERROR
  CLOSING    -> CLOSED
  CLOSED     -> IDLE       (reset)
  ERROR      -> IDLE       (reset)
"""
from __future__ import annotations

from .machine import StateMachine


class ConnectionStateMachine(StateMachine):
    STATES = {
        "IDLE":       {"transitions": {"CONNECTING"}},
        "CONNECTING": {"transitions": {"OPEN", "CLOSED", "ERROR"}},
        "OPEN":       {"transitions": {"CLOSING", "ERROR"}},
        "CLOSING":    {"transitions": {"CLOSED", "ERROR"}},
        "CLOSED":     {"transitions": {"IDLE"}},
        "ERROR":      {"transitions": {"IDLE"}},
    }
    INITIAL = "IDLE"


class TurnStateMachine(StateMachine):
    """One user-model turn.

    IDLE           -> USER_SPEAKING (VAD detected voice)
    USER_SPEAKING  -> USER_DONE (silence or semantic_vad)
    USER_DONE      -> MODEL_THINKING (LLM call dispatched)
    MODEL_THINKING  -> MODEL_SPEAKING (first TTS audio)
    MODEL_SPEAKING  -> IDLE (end of audio) | INTERRUPTED
    """

    STATES = {
        "IDLE":          {"transitions": {"USER_SPEAKING"}},
        "USER_SPEAKING": {"transitions": {"USER_DONE", "INTERRUPTED"}},
        "USER_DONE":     {"transitions": {"MODEL_THINKING", "INTERRUPTED"}},
        "MODEL_THINKING": {"transitions": {"MODEL_SPEAKING", "INTERRUPTED"}},
        "MODEL_SPEAKING": {"transitions": {"IDLE", "INTERRUPTED"}},
        "INTERRUPTED":   {"transitions": {"IDLE"}},
    }
    INITIAL = "IDLE"


class PipelineStateMachine(StateMachine):
    """The chat pipeline state for one message.

    READY        -> STREAMING (start)
    STREAMING    -> READY (end) | FAILED | INTERRUPTED
    """

    STATES = {
        "READY":       {"transitions": {"STREAMING"}},
        "STREAMING":   {"transitions": {"READY", "FAILED", "INTERRUPTED"}},
        "FAILED":      {"transitions": {"READY"}},
        "INTERRUPTED": {"transitions": {"READY"}},
    }
    INITIAL = "READY"


class MessageStateMachine(StateMachine):
    """The lifecycle of a single message in the bus.

    CREATED -> QUEUED -> STT -> LLM -> SPLIT -> TTS -> PLAYING -> COMPLETED
                          |       |       |      |      |
                          +-> FAILED <-+--------+------+
                          +-> INTERRUPTED <-+------+
    """

    STATES = {
        "CREATED":     {"transitions": {"QUEUED", "FAILED", "INTERRUPTED"}},
        "QUEUED":      {"transitions": {"STT", "LLM", "COMPLETED", "FAILED", "INTERRUPTED"}},
        "STT":         {"transitions": {"LLM", "COMPLETED", "FAILED", "INTERRUPTED"}},
        "LLM":         {"transitions": {"SPLIT", "COMPLETED", "FAILED", "INTERRUPTED"}},
        "SPLIT":       {"transitions": {"TTS", "COMPLETED", "FAILED", "INTERRUPTED"}},
        "TTS":         {"transitions": {"PLAYING", "COMPLETED", "FAILED", "INTERRUPTED"}},
        "PLAYING":     {"transitions": {"COMPLETED", "FAILED", "INTERRUPTED"}},
        "COMPLETED":   {"transitions": set()},
        "FAILED":      {"transitions": set()},
        "INTERRUPTED": {"transitions": set()},
    }
    INITIAL = "CREATED"
