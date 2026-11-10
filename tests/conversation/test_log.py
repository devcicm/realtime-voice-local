"""Tests for ConversationLog and the cancel-mode strategies."""
import pytest

from server.shared.conversation import (
    ConversationLog, Turn, CANCEL_MODES, apply_cancel_mode,
    make_note, make_summary, make_silent,
    ConversationStateMachine,
)
from server.shared.state.machine import InvalidTransitionError


# ---- Turn ----

def test_turn_default_status_is_finalized():
    t = Turn(id="x", role="user", content="hola")
    assert t.status == "finalized"
    assert t.finalized_at_ms is not None


def test_turn_to_dict_contains_all_fields():
    t = Turn(id="x", role="assistant", content="resp", reasoning="porque sí")
    d = t.to_dict()
    assert d["id"] == "x"
    assert d["role"] == "assistant"
    assert d["content"] == "resp"
    assert d["reasoning"] == "porque sí"
    assert d["status"] == "finalized"


# ---- ConversationLog: basic add/remove ----

def test_add_user_turn_increases_count():
    log = ConversationLog()
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos días")
    assert log.count() == 2


def test_to_messages_includes_added_turns():
    log = ConversationLog()
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos días")
    msgs = log.to_messages(system_prompt="you are helpful")
    assert msgs[0] == {"role": "system", "content": "you are helpful"}
    assert {"role": "user", "content": "hola"} in msgs
    assert {"role": "assistant", "content": "buenos días"} in msgs


def test_no_system_prompt_when_none_provided():
    log = ConversationLog()
    log.add_user_turn("hola")
    msgs = log.to_messages()
    assert msgs[0]["role"] == "user"


def test_assistant_reasoning_folded_into_content():
    log = ConversationLog()
    log.add_assistant_turn("hola", reasoning="user said hi")
    msgs = log.to_messages()
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert "[reasoning]" in assistant["content"]
    assert "user said hi" in assistant["content"]


def test_assistant_without_reasoning_omits_tag():
    log = ConversationLog()
    log.add_assistant_turn("hola")
    msgs = log.to_messages()
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert "[reasoning]" not in assistant["content"]


# ---- ConversationLog: streaming ----

def test_stream_user_partial_creates_then_updates():
    log = ConversationLog()
    log.stream_user_partial("h")
    log.stream_user_partial("ho")
    log.stream_user_partial("hola")
    log.commit_user_turn()
    msgs = log.to_messages()
    assert {"role": "user", "content": "hola"} in msgs
    assert log.count() == 1


def test_stream_user_partial_not_in_messages_until_committed():
    log = ConversationLog()
    log.stream_user_partial("hola")
    msgs = log.to_messages()
    # Streaming turn is excluded.
    assert all(m["role"] != "user" for m in msgs)


def test_cancel_stream_discards_partial():
    log = ConversationLog()
    log.stream_user_partial("hola mundo")
    log.cancel_stream("user")
    msgs = log.to_messages()
    assert all(m["role"] != "user" for m in msgs)
    assert log.count() == 0


# ---- ConversationLog: cancel modes ----

def test_cancel_silent_removes_turn_from_messages():
    log = ConversationLog(cancel_mode="silent")
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos días, ¿cómo")
    log.cancel_finalized("assistant", reason="barge-in")
    msgs = log.to_messages()
    # User turn remains, assistant turn gone.
    assert any(m["role"] == "user" for m in msgs)
    assert not any(m["role"] == "assistant" for m in msgs)


def test_cancel_note_injects_synthetic_system_turn():
    log = ConversationLog(cancel_mode="note")
    log.add_assistant_turn("buenos días, ¿cómo estás?")
    log.cancel_finalized("assistant", reason="barge-in")
    msgs = log.to_messages()
    synth = [m for m in msgs if m["role"] == "system" and "[Previous assistant" in m["content"]]
    assert len(synth) == 1
    assert "barge-in" in synth[0]["content"]


def test_cancel_summary_injects_first_sentence():
    log = ConversationLog(cancel_mode="summary")
    log.add_assistant_turn("Hola. Esto es una prueba larga. Más texto.")
    log.cancel_finalized("assistant", reason="barge-in")
    msgs = log.to_messages()
    synth = [m for m in msgs if m["role"] == "system" and "[Previous assistant" in m["content"]]
    assert len(synth) == 1
    # First sentence only.
    assert "Esto es una prueba larga" not in synth[0]["content"]
    assert "Hola." in synth[0]["content"]


def test_cancel_truncates_long_partial():
    log = ConversationLog(cancel_mode="note")
    long_text = "palabra " * 50
    log.add_assistant_turn(long_text)
    log.cancel_finalized("assistant", reason="barge-in")
    msgs = log.to_messages()
    synth = [m for m in msgs if m["role"] == "system" and "Partial content" in m["content"]][0]
    # Must be truncated.
    assert "..." in synth["content"]


def test_cancel_modes_registry_covers_all():
    assert set(CANCEL_MODES) == {"silent", "note", "summary"}
    for mode in CANCEL_MODES:
        result = apply_cancel_mode(mode, "assistant", "test", "some text")
        # 'silent' returns None; others return a Turn.
        if mode == "silent":
            assert result is None
        else:
            assert isinstance(result, Turn)


def test_apply_cancel_mode_unknown_returns_none():
    assert apply_cancel_mode("unknown", "assistant", "x", "y") is None


# ---- ConversationLog: sliding window ----

def test_sliding_window_truncates_old_turns():
    log = ConversationLog(window_size=3)
    for i in range(5):
        log.add_user_turn(f"u{i}")
    msgs = log.to_messages()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 3
    # The three most recent.
    assert user_msgs[0]["content"] == "u2"
    assert user_msgs[-1]["content"] == "u4"


def test_sliding_window_with_summarizer():
    """With strategy=summarize, evicted turns are batched into a synthetic
    system turn. The window must still hold."""
    summaries = []

    def summarizer(turns):
        summaries.append([t.content for t in turns])
        return f"[Summary of {len(turns)} earlier turns]"

    # Window big enough that we keep some user turns + at least one summary.
    log = ConversationLog(window_size=6, overflow_strategy="summarize", summarizer=summarizer)
    for i in range(8):
        log.add_user_turn(f"u{i}")
    msgs = log.to_messages()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    synth = [m for m in msgs if m["role"] == "system" and "Summary" in m["content"]]
    # The most recent user turn is preserved.
    assert user_msgs[-1]["content"] == "u7"
    # A synthetic summary is present.
    assert len(synth) >= 1
    # The summarizer was invoked at least once.
    assert len(summaries) >= 1
    # Total linear sequence fits the window.
    assert len(msgs) <= 6


def test_sliding_window_summarizer_does_not_overshoot_window():
    """After summarization, total linear sequence must be <= window_size."""

    def summarizer(turns):
        return f"[Summary of {len(turns)} earlier turns]"

    for window in (3, 5, 10):
        log = ConversationLog(window_size=window, overflow_strategy="summarize", summarizer=summarizer)
        for i in range(20):
            log.add_user_turn(f"u{i}")
        msgs = log.to_messages()
        assert len(msgs) <= window, f"overshoot with window={window}: {len(msgs)} msgs"


def test_set_window_size_truncates():
    log = ConversationLog(window_size=10)
    for i in range(8):
        log.add_user_turn(f"u{i}")
    log.set_window_size(3)
    msgs = log.to_messages()
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 3


def test_clear_empties_log():
    log = ConversationLog()
    log.add_user_turn("x")
    log.add_assistant_turn("y")
    log.clear()
    assert log.count() == 0
    assert log.to_messages() == []


# ---- ConversationLog: events ----

def test_turn_added_event_fires():
    log = ConversationLog()
    events = []
    log.on("turnAdded", lambda e: events.append(e))
    log.add_user_turn("hola")
    assert len(events) == 1
    assert events[0]["turn"]["content"] == "hola"


def test_turn_canceled_event_fires():
    log = ConversationLog(cancel_mode="note")
    events = []
    log.on("turnCanceled", lambda e: events.append(e))
    log.add_assistant_turn("hola")
    log.cancel_finalized("assistant", reason="barge-in")
    assert len(events) == 1
    assert events[0]["reason"] == "barge-in"
    assert events[0]["event"] == "turnCanceled"


def test_cleared_event_fires():
    log = ConversationLog()
    events = []
    log.on("cleared", lambda e: events.append(e))
    log.add_user_turn("x")
    log.clear()
    assert len(events) == 1
    assert events[0]["event"] == "cleared"


def test_unknown_event_raises():
    log = ConversationLog()
    with pytest.raises(ValueError):
        log.on("bogus", lambda e: None)


# ---- ConversationLog: realtime events ----

def test_to_realtime_events_emits_create_for_each_turn():
    log = ConversationLog()
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos días")
    events = log.to_realtime_events()
    creates = [e for e in events if e["type"] == "conversation.item.create"]
    assert len(creates) == 2
    user = next(e for e in creates if e["item"]["role"] == "user")
    assert user["item"]["content"][0]["text"] == "hola"


def test_to_realtime_events_excludes_canceled():
    log = ConversationLog(cancel_mode="note")
    log.add_assistant_turn("cancel me")
    log.cancel_finalized("assistant", reason="barge-in")
    events = log.to_realtime_events()
    # No assistant turn in the events.
    assistant_items = [e for e in events if e["item"]["role"] == "assistant"]
    assert len(assistant_items) == 0
    # The synthetic system turn IS included so OpenAI sees the interruption.
    system_items = [e for e in events if e["item"]["role"] == "system"]
    assert len(system_items) == 1


# ---- ConversationStateMachine ----

def test_conversation_state_machine_initial():
    m = ConversationStateMachine()
    assert m.current == "IDLE"


def test_conversation_state_machine_happy_path():
    m = ConversationStateMachine()
    m.transition("STREAMING")
    m.transition("DELIVERED")
    m.transition("COMMITTED")
    assert m.current == "COMMITTED"
    assert m.is_terminal()


def test_conversation_state_machine_barge_in_during_stream():
    m = ConversationStateMachine()
    m.transition("STREAMING")
    m.transition("CANCELED")
    m.transition("IDLE")
    assert m.current == "IDLE"


def test_conversation_state_machine_barge_in_after_delivery():
    m = ConversationStateMachine()
    m.transition("STREAMING")
    m.transition("DELIVERED")
    m.transition("CANCELED")
    m.transition("IDLE")
    assert m.current == "IDLE"


def test_conversation_state_machine_cannot_revive_committed():
    m = ConversationStateMachine()
    m.transition("STREAMING")
    m.transition("DELIVERED")
    m.transition("COMMITTED")
    with pytest.raises(InvalidTransitionError):
        m.transition("CANCELED")
