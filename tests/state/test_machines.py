"""Tests for the FSM base and the four concrete state machines."""
import pytest

from server.shared.state import (
    StateMachine, InvalidTransitionError,
    ConnectionStateMachine,
    TurnStateMachine,
    PipelineStateMachine,
    MessageStateMachine,
)


# ---- Base StateMachine ----

def test_initial_state():
    m = ConnectionStateMachine()
    assert m.current == "IDLE"
    assert m.name == "ConnectionStateMachine"  # derived from class


def test_first_history_record_is_system_default():
    m = ConnectionStateMachine()
    assert m.history[0].to_state == "IDLE"
    assert m.history[0].reason == "system-default"


def test_valid_transition_appends_history():
    m = ConnectionStateMachine()
    m.transition("CONNECTING", reason="start")
    assert m.current == "CONNECTING"
    assert len(m.history) == 2
    assert m.history[-1].from_state == "IDLE"
    assert m.history[-1].to_state == "CONNECTING"
    assert m.history[-1].reason == "start"


def test_invalid_transition_raises():
    m = ConnectionStateMachine()
    with pytest.raises(InvalidTransitionError):
        m.transition("OPEN")  # IDLE -> OPEN not allowed


def test_transition_to_self_is_noop():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    before = len(m.history)
    m.transition("CONNECTING")  # same
    assert len(m.history) == before  # no new record


def test_force_skips_table():
    m = ConnectionStateMachine()
    m.force("OPEN", reason="recovery")
    assert m.current == "OPEN"
    assert m.history[-1].reason.startswith("[force]")


def test_is_in():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    assert m.is_in("CONNECTING")
    assert m.is_in("IDLE", "CONNECTING")
    assert not m.is_in("OPEN")


def test_is_terminal():
    m = MessageStateMachine()
    assert not m.is_terminal()
    m.transition("QUEUED")
    m.transition("COMPLETED")
    assert m.is_terminal()


def test_observer_called_on_transition():
    m = ConnectionStateMachine()
    seen = []
    m.on(lambda machine, rec: seen.append((machine.current, rec.to_state)))
    m.transition("CONNECTING")
    m.transition("OPEN")
    assert seen == [("CONNECTING", "CONNECTING"), ("OPEN", "OPEN")]


def test_observer_error_does_not_break_transition():
    m = ConnectionStateMachine()
    def bad(_machine, _rec): raise RuntimeError("oops")
    m.on(bad)
    m.transition("CONNECTING")  # must not raise
    assert m.current == "CONNECTING"


def test_snapshot_shape():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    s = m.snapshot()
    assert s["name"] == "ConnectionStateMachine"
    assert s["current"] == "CONNECTING"
    assert isinstance(s["history"], list)
    assert all("from" in r and "to" in r for r in s["history"])


# ---- ConnectionStateMachine ----

def test_connection_full_lifecycle():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    m.transition("OPEN")
    m.transition("CLOSING")
    m.transition("CLOSED")
    m.transition("IDLE")  # reset
    assert m.current == "IDLE"


def test_connection_can_fail_from_connecting():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    m.transition("ERROR")
    m.transition("IDLE")
    assert m.current == "IDLE"


def test_connection_open_can_error():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    m.transition("OPEN")
    m.transition("ERROR")
    assert m.current == "ERROR"


def test_connection_closed_cannot_go_back_to_open():
    m = ConnectionStateMachine()
    m.transition("CONNECTING")
    m.transition("OPEN")
    m.transition("CLOSING")
    m.transition("CLOSED")
    with pytest.raises(InvalidTransitionError):
        m.transition("OPEN")  # must go through IDLE


# ---- TurnStateMachine ----

def test_turn_full_happy_path():
    m = TurnStateMachine()
    m.transition("USER_SPEAKING")
    m.transition("USER_DONE")
    m.transition("MODEL_THINKING")
    m.transition("MODEL_SPEAKING")
    m.transition("IDLE")
    assert m.current == "IDLE"


def test_turn_interruption_from_speaking():
    m = TurnStateMachine()
    m.transition("USER_SPEAKING")
    m.transition("INTERRUPTED")
    m.transition("IDLE")
    assert m.current == "IDLE"


def test_turn_cannot_skip_user_done():
    m = TurnStateMachine()
    m.transition("USER_SPEAKING")
    with pytest.raises(InvalidTransitionError):
        m.transition("MODEL_SPEAKING")  # must go through USER_DONE -> MODEL_THINKING


def test_turn_interruption_from_any_state():
    """Each state that allows transition to INTERRUPTED can be interrupted."""
    paths = {
        "USER_SPEAKING": ["USER_SPEAKING"],
        "USER_DONE":     ["USER_SPEAKING", "USER_DONE"],
        "MODEL_THINKING": ["USER_SPEAKING", "USER_DONE", "MODEL_THINKING"],
        "MODEL_SPEAKING": ["USER_SPEAKING", "USER_DONE", "MODEL_THINKING", "MODEL_SPEAKING"],
    }
    for target, path in paths.items():
        m = TurnStateMachine()
        for s in path:
            m.transition(s)
        m.transition("INTERRUPTED")
        assert m.current == "INTERRUPTED", f"failed for {target}"


# ---- PipelineStateMachine ----

def test_pipeline_ready_streaming_ready():
    m = PipelineStateMachine()
    m.transition("STREAMING")
    m.transition("READY")
    assert m.current == "READY"


def test_pipeline_can_fail_mid_stream():
    m = PipelineStateMachine()
    m.transition("STREAMING")
    m.transition("FAILED")
    m.transition("READY")
    assert m.current == "READY"


# ---- MessageStateMachine ----

def test_message_full_lifecycle():
    m = MessageStateMachine()
    for state in ["QUEUED", "LLM", "SPLIT", "TTS", "PLAYING", "COMPLETED"]:
        m.transition(state)
    assert m.current == "COMPLETED"
    assert m.is_terminal()


def test_message_terminal_states_have_no_transitions():
    m = MessageStateMachine()
    m.transition("QUEUED")
    m.transition("COMPLETED")
    with pytest.raises(InvalidTransitionError):
        m.transition("PLAYING")  # terminal, no way out


def test_message_can_fail_from_any_active_state():
    paths = {
        "QUEUED":   ["QUEUED"],
        "LLM":      ["QUEUED", "LLM"],
        "SPLIT":    ["QUEUED", "LLM", "SPLIT"],
        "TTS":      ["QUEUED", "LLM", "SPLIT", "TTS"],
        "PLAYING":  ["QUEUED", "LLM", "SPLIT", "TTS", "PLAYING"],
    }
    for target, path in paths.items():
        m = MessageStateMachine()
        for s in path:
            m.transition(s)
        m.transition("FAILED")
        assert m.current == "FAILED", f"failed for {target}"


def test_message_can_be_interrupted():
    m = MessageStateMachine()
    m.transition("QUEUED")
    m.transition("LLM")
    m.transition("INTERRUPTED")
    assert m.is_terminal()


def test_message_can_skip_stt_if_text_input():
    """Direct text input should skip STT."""
    m = MessageStateMachine()
    m.transition("QUEUED")
    m.transition("LLM")  # skip STT
    assert m.current == "LLM"
