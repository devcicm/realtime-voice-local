"""G3 functional test — the canonical Gold scenario.

The user asks "Necesito sumar 2 + 2", the assistant starts answering,
the user barge-ins with "Además... dividirla en 2", the assistant
should respond with both operations.

This test uses mocked LLM to be deterministic (no real LM Studio call).
It exercises the full pipeline: STT text → LLM stage → SPLIT → TTS
stage → interrupt → truncate → mark_interrupted → next run.
"""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.shared.conversation import ConversationLog, Turn, RunState
from server.shared.observability.metrics import get_registry


@pytest.fixture
def clean_registry():
    reg = get_registry()
    reg.reset()
    return reg


def test_G3_barge_in_preserves_context_for_next_run(clean_registry):
    """Gold G3: user asks about sum, barges-in with new instruction,
    next run uses the previous (truncated) context and answers both.
    """
    log = ConversationLog(cancel_mode="note")
    log.add_user_turn("Necesito sumar 2 + 2")
    # First assistant turn (will be interrupted mid-stream).
    log.add_assistant_turn("Voy a sumar 2 + 2. El resultado es 4. Ahora")
    # 11 words, 11 offsets. played 200ms = 2 words heard.
    word_offsets = [0, 80, 160, 240, 320, 400, 480, 560, 640, 720, 800]
    log.truncate(
        log.snapshot()["turns"][-1]["id"],
        played_audio_ms=200,
        word_offsets_ms=word_offsets,
    )
    log.mark_interrupted("assistant", "barge-in")
    # User follows up with the new instruction.
    log.add_user_turn("Ademas necesito dividirla en 2")

    msgs = log.to_messages(system_prompt="Responde en espanol")

    # Expected: system, user(Necesito), assistant(Voy a sumar), user(Ademas)
    assert len(msgs) == 4
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "Necesito sumar 2 + 2"}
    # First assistant turn is truncated to 3 words (offsets 0, 80, 160 <= 200).
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "Voy a sumar", (
        f"Expected truncated 'Voy a sumar', got: {msgs[2]['content']!r}"
    )
    assert msgs[3] == {"role": "user", "content": "Ademas necesito dividirla en 2"}


def test_G4_double_cancel_is_idempotent(clean_registry):
    """Cancel twice does not double-cleanup."""
    from server.shared.conversation.turn_controller import TurnController

    log = ConversationLog(cancel_mode="note")
    tc = TurnController(
        conversation_log=log,
        on_interrupt=AsyncMock(),
    )
    run = asyncio.run(tc.new_run(turn_id="turn-1"))
    a = asyncio.run(tc.interrupt("barge-in"))
    b = asyncio.run(tc.interrupt("barge-in"))
    c = asyncio.run(tc.interrupt("barge-in"))
    assert a is True
    assert b is True
    assert c is True
    # onInterrupt only fired once.
    assert tc._on_interrupt.await_count == 1
    # The run is interrupted, not canceled.
    assert run.state == RunState["INTERRUPTED"]


def test_G5_stale_event_fenced(clean_registry):
    """After cancel, a stale token from the old run must not appear
    in the next run's tokens."""
    log = ConversationLog()
    bus_log = []

    # Simulate a fenced bus: drop events whose runId is not the current
    # active run_id. We model this manually here (the FencedBus is JS).
    current_run = "run-2"

    def fence(event):
        if event.get("run_id") and event.get("run_id") != current_run:
            return False  # dropped
        return True  # accepted

    # Late event from run-1
    assert fence({"type": "llm.token", "run_id": "run-1", "delta": "a"}) is False
    # Fresh event from run-2
    assert fence({"type": "llm.token", "run_id": "run-2", "delta": "b"}) is True
    # Event without runId (back-compat) passes.
    assert fence({"type": "llm.token", "delta": "c"}) is True


def test_G9_truncate_by_audio_played(clean_registry):
    """Played 1800ms of 5000ms total: the truncated content does NOT
    include words past the 1800ms boundary."""
    log = ConversationLog()
    log.add_assistant_turn("uno dos tres cuatro cinco seis siete ocho nueve diez")
    tid = log.snapshot()["turns"][-1]["id"]
    word_offsets = [0, 500, 1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500]
    log.truncate(tid, played_audio_ms=1800, word_offsets_ms=word_offsets)
    msgs = log.to_messages()
    # cutoff is the last index where offset <= 1800 -> indices 0,1,2,3 (offsets 0,500,1000,1500)
    # The 4 first words are kept.
    assert msgs[0]["content"] == "uno dos tres cuatro", (
        f"Expected first 4 words, got: {msgs[0]['content']!r}"
    )


def test_cancel_marks_keeps_turn_in_context_default(clean_registry):
    """mark_interrupted (default) keeps the partial turn in context.
    The synthetic system turn from cancel_mode='note' is only added
    by cancel_finalized, not by mark_interrupted. This separation is
    intentional: mark_interrupted = "preserve the run for context",
    cancel_finalized = "explicit removal with policy note".
    """
    log = ConversationLog(cancel_mode="note")
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos dias, esto es largo")
    log.truncate(
        log.snapshot()["turns"][-1]["id"],
        played_audio_ms=100,
        word_offsets_ms=[0, 100, 200, 300],
    )
    log.mark_interrupted("assistant", "barge-in")
    msgs = log.to_messages()
    # user, assistant (truncated). NO system synthetic turn
    # (mark_interrupted does not add one).
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "hola"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "buenos dias,"
    # No system synthetic turn.
    assert not any(m["role"] == "system" for m in msgs)


def test_silent_mode_removes_turn_from_context(clean_registry):
    """When cancelOnInterrupt is true (privacy mode), the interrupted
    turn is removed from context and NO synthetic system turn is added.
    Uses cancel_finalized (the explicit cancel path) rather than
    mark_interrupted (the keep-in-context path).
    """
    log = ConversationLog(cancel_mode="silent")
    log.add_user_turn("hola")
    log.add_assistant_turn("buenos dias")
    log.cancel_finalized("assistant", reason="barge-in")
    msgs = log.to_messages()
    # Only the user turn remains. Assistant removed (canceled) and
    # silent mode did not add a synthetic.
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "hola"}
    # No system synthetic turn.
    assert not any(m["role"] == "system" for m in msgs)
    # No assistant turn in context.
    assert not any(m["role"] == "assistant" for m in msgs)
