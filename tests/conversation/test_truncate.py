"""Tests for ConversationLog.truncate."""
import pytest

from server.shared.conversation import ConversationLog


def _setup_assistant_turn(text: str):
    log = ConversationLog()
    log.add_assistant_turn(text)
    return log, log.snapshot()["turns"][0]["id"]


def test_truncate_basic_cutoff():
    log, tid = _setup_assistant_turn("uno dos tres cuatro cinco seis")
    # offsets in ms: uno=0, dos=100, tres=200, cuatro=300, cinco=400, seis=500
    offsets = [0, 100, 200, 300, 400, 500]
    out = log.truncate(tid, played_audio_ms=250, word_offsets_ms=offsets)
    assert out is not None
    assert out.truncated_at_ms == 250
    # "uno dos tres" — 3 words where index < 3
    assert out.content == "uno dos tres"


def test_truncate_idempotent():
    log, tid = _setup_assistant_turn("uno dos tres")
    log.truncate(tid, played_audio_ms=50, word_offsets_ms=[0, 100, 200])
    # Save the content after first truncate.
    after_first = log.snapshot()["turns"][0]["content"]
    # Second call is a no-op (truncated_at_ms already set).
    log.truncate(tid, played_audio_ms=200, word_offsets_ms=[0, 100, 200])
    after_second = log.snapshot()["turns"][0]["content"]
    # Second call did not change the content.
    assert after_first == after_second


def test_truncate_no_offsets_keeps_full():
    log, tid = _setup_assistant_turn("uno dos tres")
    out = log.truncate(tid, played_audio_ms=100, word_offsets_ms=None)
    assert out.content == "uno dos tres"
    assert out.truncated_at_ms is None  # not truncated


def test_truncate_played_zero_keeps_full():
    log, tid = _setup_assistant_turn("uno dos tres")
    out = log.truncate(tid, played_audio_ms=0, word_offsets_ms=[0, 100, 200])
    assert out.content == "uno dos tres"


def test_truncate_all_played():
    log, tid = _setup_assistant_turn("uno dos tres")
    out = log.truncate(tid, played_audio_ms=9999, word_offsets_ms=[0, 100, 200])
    assert out.content == "uno dos tres"  # all 3 words heard


def test_truncate_clamps_to_word_count():
    """If played_audio_ms exceeds last offset, the last word is the
    cutoff (clamped by len(words) for safety)."""
    log, tid = _setup_assistant_turn("uno dos")
    # Bogus offsets array longer than the word list.
    out = log.truncate(tid, played_audio_ms=5000, word_offsets_ms=[0, 100, 200, 300, 400])
    assert out.content == "uno dos"  # clamped at len(words)


def test_truncate_no_word_played():
    """If played_audio_ms < first offset, no word is heard → empty content."""
    log, tid = _setup_assistant_turn("uno dos tres")
    out = log.truncate(tid, played_audio_ms=10, word_offsets_ms=[100, 200, 300])
    assert out.content == ""


def test_truncate_does_not_affect_to_messages_other_turns():
    log = ConversationLog()
    log.add_user_turn("hola")
    log.add_assistant_turn("respuesta larga uno dos tres")
    log.add_user_turn("adios")
    msg_id = log.snapshot()["turns"][1]["id"]
    log.truncate(msg_id, played_audio_ms=5, word_offsets_ms=[100, 200, 300])
    msgs = log.to_messages()
    # user turns untouched, assistant truncated (no word was heard)
    assert msgs[0] == {"role": "user", "content": "hola"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == ""
    assert msgs[2] == {"role": "user", "content": "adios"}


def test_truncate_keeps_turn_in_context():
    """Truncation does NOT exclude the turn from context (unlike
    cancel). The LLM should still see the (truncated) response."""
    log = ConversationLog()
    log.add_assistant_turn("uno dos tres cuatro cinco")
    tid = log.snapshot()["turns"][0]["id"]
    log.truncate(tid, played_audio_ms=150, word_offsets_ms=[0, 100, 200, 300, 400])
    msgs = log.to_messages()
    # Assistant still in context, but content is truncated.
    assistant = [m for m in msgs if m["role"] == "assistant"][0]
    assert assistant["content"] == "uno dos"


def test_truncate_emits_event():
    log, tid = _setup_assistant_turn("uno dos tres")
    events = []
    log.on("turnTruncated", lambda e: events.append(e))
    log.truncate(tid, played_audio_ms=100, word_offsets_ms=[0, 100, 200])
    assert len(events) == 1
    assert events[0]["event"] == "turnTruncated"


def test_truncate_handles_words_split_by_multiple_spaces():
    log, tid = _setup_assistant_turn("uno   dos\ttres")
    # The original turn preserved its whitespace; after truncate, the
    # split() without arg collapses it to 3 words, of which the first 2
    # are kept.
    log.truncate(tid, played_audio_ms=150, word_offsets_ms=[0, 100, 200])
    out = log.snapshot()["turns"][0]
    kept = out["content"].split()
    assert kept == ["uno", "dos"]


def test_truncate_finds_turn_by_role_when_id_unknown():
    """If the turn_id is stale (e.g. log cleared and restarted), use the
    most recent assistant turn as fallback."""
    log = ConversationLog()
    log.add_assistant_turn("uno dos")
    # Pass a bogus id; the function should find the latest assistant turn.
    out = log.truncate("nonexistent", played_audio_ms=50, word_offsets_ms=[0, 100])
    assert out is not None
    assert out.content in ("uno", "uno dos")  # depending on cutoff
