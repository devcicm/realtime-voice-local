"""Tests for the new 'interrupted' status and mark_interrupted()."""
import time
import pytest

from server.shared.conversation import (
    ConversationLog, Turn, CANCEL_MODES, apply_cancel_mode,
    ConversationStateMachine,
)


# ---- mark_interrupted keeps the turn in context ----

def test_mark_interrupted_keeps_turn_in_context():
    log = ConversationLog()
    log.add_assistant_turn("Hola, esto es una respuesta")
    log.mark_interrupted("assistant", "barge-in", "Hola, esto es una resp")
    msgs = log.to_messages()
    # The turn is INCLUDED (interrupted != canceled).
    assert any(m["role"] == "assistant" and "resp" in m["content"] for m in msgs), \
        f"interrupted turn missing from messages: {msgs}"


def test_mark_interrupted_keeps_turn_in_log():
    log = ConversationLog()
    log.add_assistant_turn("Hola, esto es una respuesta")
    log.mark_interrupted("assistant", "barge-in")
    snap = log.snapshot()
    assert snap["turns"][0]["status"] == "interrupted"
    assert len(snap["turns"]) == 1  # not removed


def test_mark_interrupted_records_reason_and_timestamp():
    log = ConversationLog()
    log.add_assistant_turn("respuesta larga")
    log.mark_interrupted("assistant", "barge-in", "respuesta")
    snap = log.snapshot()
    t = snap["turns"][0]
    assert t["interrupted_reason"] == "barge-in"
    assert t["interrupted_at_ms"] is not None
    assert t["interrupted_at_ms"] >= t["created_at_ms"]


def test_mark_interrupted_stores_partial_text():
    log = ConversationLog()
    log.add_assistant_turn("Hola mundo")
    log.mark_interrupted("assistant", "barge-in", "Hola mun")
    snap = log.snapshot()
    assert snap["turns"][0]["partial_at_interrupt"] == "Hola mun"


def test_mark_interrupted_idempotent():
    log = ConversationLog()
    log.add_assistant_turn("resp")
    log.mark_interrupted("assistant", "first-barge-in")
    first_ts = log.snapshot()["turns"][0]["interrupted_at_ms"]
    time.sleep(0.005)
    log.mark_interrupted("assistant", "second-barge-in")
    second_ts = log.snapshot()["turns"][0]["interrupted_at_ms"]
    assert second_ts > first_ts
    # Still only one turn.
    assert len(log.snapshot()["turns"]) == 1


def test_mark_interrupted_emits_event():
    log = ConversationLog()
    events = []
    log.on("turnInterrupted", lambda e: events.append(e))
    log.add_assistant_turn("resp")
    log.mark_interrupted("assistant", "barge-in")
    assert len(events) == 1
    assert events[0]["event"] == "turnInterrupted"
    assert events[0]["reason"] == "barge-in"


# ---- cancel_finalized still excludes from context ----

def test_cancel_finalized_excludes_turn():
    log = ConversationLog(cancel_mode="note")
    log.add_assistant_turn("resp")
    log.cancel_finalized("assistant", "barge-in")
    msgs = log.to_messages()
    assert not any(m["role"] == "assistant" for m in msgs)


def test_cancel_finalized_emits_synthetic_in_note_mode():
    log = ConversationLog(cancel_mode="note")
    log.add_assistant_turn("resp")
    log.cancel_finalized("assistant", "barge-in")
    msgs = log.to_messages()
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert any("Previous assistant" in m["content"] for m in sys_msgs)


# ---- mark_interrupted does NOT emit synthetic ----

def test_mark_interrupted_no_synthetic_in_default():
    """mark_interrupted should NOT inject a synthetic system turn."""
    log = ConversationLog(cancel_mode="note")  # even with note mode
    log.add_assistant_turn("resp")
    log.mark_interrupted("assistant", "barge-in")
    msgs = log.to_messages()
    # No system turn from cancel — interrupted is kept as-is.
    sys_msgs = [m for m in msgs if m["role"] == "system" and "Previous assistant" in m["content"]]
    assert len(sys_msgs) == 0


# ---- turn status field validates new state ----

def test_turn_status_accepts_interrupted():
    t = Turn(id="x", role="assistant", content="x", status="interrupted")
    assert t.status == "interrupted"


def test_turn_status_rejects_garbage():
    with pytest.raises(Exception):
        Turn(id="x", role="assistant", content="x", status="bogus")


# ---- mixed scenario: barge-in then continue ----

def test_barge_in_preserves_conversation_history():
    """Full scenario: user asks, assistant responds, user barges in,
    user asks new question. The LLM should see the partial response."""
    log = ConversationLog()

    # Turn 1: user asks
    log.add_user_turn("Cuentame sobre Python")
    # Turn 2: assistant responds (partial, interrupted)
    log.add_assistant_turn("Python es un lenguaje de programacion creado por Guido van Rossum en 1991. Es conocido por su")
    log.mark_interrupted("assistant", "barge-in", "Python es un lenguaje de programacion creado por")
    # Turn 3: user follows up
    log.add_user_turn("Y por que se llama Python?")

    msgs = log.to_messages()
    # Should have: user-1, assistant-1 (interrupted, kept), user-2
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "user"]
    # The assistant turn has the partial text.
    assert "Python es un lenguaje" in msgs[1]["content"]
    # No synthetic system turn was injected.
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert len(sys_msgs) == 0


def test_barge_in_then_cancel_then_assistant_still_works():
    """Privacy mode still works: barge-in with cancelOnInterrupt=true
    actually removes the turn from context."""
    log = ConversationLog(cancel_mode="note")
    log.add_user_turn("Hola")
    log.add_assistant_turn("Buen dia, en que puedo ayudarte?")
    log.cancel_finalized("assistant", "barge-in")  # privacy mode
    log.add_user_turn("Como te llamas?")

    msgs = log.to_messages()
    roles = [m["role"] for m in msgs]
    # Synthetics are appended at the end of the sequence.
    # Order: user-1, user-2, system (synthetic).
    assert roles == ["user", "user", "system"]
    # The original assistant content is NOT in any ASSISTANT message
    # (the synthetic may quote it as part of the interruption note).
    assert not any(
        m["role"] == "assistant" and "Buen dia" in m["content"]
        for m in msgs
    )
    # The synthetic system message describes the interruption.
    sys_msgs = [m for m in msgs if m["role"] == "system"]
    assert any("Previous assistant" in m["content"] for m in sys_msgs)


# ---- interrupted turns visible in inspector ----

def test_interrupted_visible_in_history():
    log = ConversationLog()
    log.add_user_turn("u1")
    log.add_assistant_turn("a1")
    log.mark_interrupted("assistant", "barge-in")
    log.add_user_turn("u2")
    history = log.history()
    statuses = [t["status"] for t in history]
    assert statuses == ["finalized", "interrupted", "finalized"]


# ---- cancel modes don't activate on mark_interrupted ----

def test_cancel_modes_registry_unchanged_by_interrupted():
    assert CANCEL_MODES == ("silent", "note", "summary")
    # mark_interrupted should not invoke any cancel mode.
    log = ConversationLog(cancel_mode="silent")
    log.add_assistant_turn("a1")
    log.mark_interrupted("assistant", "barge-in")
    msgs = log.to_messages()
    # silent cancel mode would remove; mark_interrupted should keep.
    assert any(m["role"] == "assistant" for m in msgs)

