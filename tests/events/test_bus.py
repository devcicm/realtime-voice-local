"""Tests for the event system: envelope, bus, channels, types, voice bridge."""
import pytest

from server.shared.events import (
    Event, EventBus, EventType,
    VoiceChannel, ChatChannel, EventBusInstance,
    VoiceBridge,
)
from server.shared.events.bus import Channel


@pytest.fixture
def fresh_bus():
    """Reset the singleton bus before each test."""
    bus = EventBus()
    saved = EventBusInstance
    # Replace the singleton's internal state with our fresh one.
    EventBusInstance._subscribers = bus._subscribers
    EventBusInstance._all_subscribers = bus._all_subscribers
    yield EventBusInstance
    EventBusInstance._subscribers = saved._subscribers
    EventBusInstance._all_subscribers = saved._all_subscribers


def test_event_has_required_fields():
    e = Event(type=EventType.LLM_TOKEN_DELTA, source="llm.stage", payload={"text": "hi"})
    assert e.type == "llm.token.delta"
    assert e.source == "llm.stage"
    assert e.payload == {"text": "hi"}
    assert e.id != ""
    assert e.ts != ""
    assert e.ts_ms > 0


def test_event_to_dict_roundtrip():
    e = Event(type="x", source="y", payload={"a": 1}, correlation_id="trace-1")
    d = e.to_dict()
    assert d["type"] == "x"
    assert d["correlation_id"] == "trace-1"
    assert d["payload"] == {"a": 1}


def test_event_clone_with_overrides():
    e = Event(type="x", source="y", payload={"a": 1})
    e2 = e.clone(type="z", source="w")
    assert e2.type == "z"
    assert e2.source == "w"
    assert e2.payload == {"a": 1}
    assert e2.id == e.id  # preserved


def test_event_types_match_catalog():
    """All enum values must match shared/events.json keys."""
    expected = {
        "USER_INPUT_RECEIVED", "STT_TRANSCRIBED", "LLM_TOKEN_DELTA", "LLM_RESPONSE_DONE",
        "TTS_CHUNK_SENT", "TTS_AUDIO_DELTA", "TTS_AUDIO_END", "AUDIO_PLAYED",
        "TRANSCRIPT_DELTA", "MESSAGE_DISPLAYED",
        "CONN_OPENING", "CONN_OPEN", "CONN_CLOSING", "CONN_CLOSED", "CONN_FAILED",
        "CONN_RECONNECTING", "CONN_RECONNECTED",
        "VAD_SPEECH_START", "VAD_SPEECH_END", "VAD_TURN_COMPLETE",
        "INTERRUPT_REQUESTED", "INTERRUPT_ACK", "MODE_CHANGED", "CONFIG_UPDATED",
        "ERROR_RAISED", "LATENCY_OBSERVED", "CIRCUIT_BREAKER_CHANGED", "STATE_CHANGED",
    }
    actual = {e.name for e in EventType}
    assert actual == expected


def test_event_type_values_dotted():
    """All values must be dotted (lowercase)."""
    for e in EventType:
        assert "." in e.value, f"{e.name} = {e.value!r} is not dotted"
        assert e.value == e.value.lower()


def test_bus_publish_routes_to_subscribers(fresh_bus):
    received = []
    fresh_bus.subscribe(EventType.LLM_TOKEN_DELTA, lambda e: received.append(e))
    evt = Event(type=EventType.LLM_TOKEN_DELTA, source="test", payload={"i": 1})
    fresh_bus.publish(evt)
    assert len(received) == 1
    assert received[0].id == evt.id


def test_bus_subscribe_returns_unsubscribe(fresh_bus):
    received = []
    unsub = fresh_bus.subscribe("x", lambda e: received.append(e))
    fresh_bus.emit("x", {"a": 1})
    unsub()
    fresh_bus.emit("x", {"a": 2})
    assert len(received) == 1


def test_bus_subscriber_errors_dont_crash_publish(fresh_bus):
    received = []

    def bad(_):
        raise RuntimeError("boom")

    fresh_bus.subscribe("x", bad)
    fresh_bus.subscribe("x", lambda e: received.append(e))
    fresh_bus.emit("x", {"a": 1})
    assert len(received) == 1  # second handler still ran


def test_bus_subscribe_all_fires_for_every_event(fresh_bus):
    seen = []
    fresh_bus.subscribe_all(lambda e: seen.append(e.type))
    fresh_bus.emit("a", {})
    fresh_bus.emit("b", {})
    fresh_bus.emit("c", {})
    assert seen == ["a", "b", "c"]


def test_channel_emits_with_source(fresh_bus):
    ch = Channel("voice", fresh_bus)
    received = []
    ch.subscribe(EventType.VAD_SPEECH_START, lambda e: received.append(e))
    ch.emit(EventType.VAD_SPEECH_START, {"level": 0.5})
    assert len(received) == 1
    assert received[0].source == "voice"


def test_voice_channel_and_chat_channel_are_independent(fresh_bus):
    voice_seen = []
    chat_seen = []
    VoiceChannel.subscribe(EventType.VAD_SPEECH_START, lambda e: voice_seen.append(e))
    ChatChannel.subscribe(EventType.LLM_TOKEN_DELTA, lambda e: chat_seen.append(e))

    VoiceChannel.emit(EventType.VAD_SPEECH_START, {"level": 0.7})
    assert len(voice_seen) == 1
    assert len(chat_seen) == 0

    ChatChannel.emit(EventType.LLM_TOKEN_DELTA, {"text": "hi"})
    assert len(voice_seen) == 1
    assert len(chat_seen) == 1


def test_correlation_id_propagates_across_channels(fresh_bus):
    corr = "trace-abc-123"
    seen = []
    VoiceChannel.subscribe(EventType.TTS_AUDIO_DELTA, lambda e: seen.append(e))
    ChatChannel.subscribe(EventType.LLM_TOKEN_DELTA, lambda e: seen.append(e))

    VoiceChannel.emit(EventType.TTS_AUDIO_DELTA, {"chunk_id": "c1"}, correlation_id=corr)
    ChatChannel.emit(EventType.LLM_TOKEN_DELTA, {"text": "hi"}, correlation_id=corr)

    assert len(seen) == 2
    assert all(e.correlation_id == corr for e in seen)


def test_causation_id_links_events(fresh_bus):
    seen = []
    ChatChannel.subscribe(EventType.LLM_TOKEN_DELTA, lambda e: seen.append(e))
    parent = Event(type=EventType.USER_INPUT_RECEIVED, source="input", payload={"text": "hi"})
    ChatChannel.emit(EventType.LLM_TOKEN_DELTA, {"text": "ho"}, causation_id=parent.id)
    assert seen[0].causation_id == parent.id


def test_voice_bridge_basic_bind_and_lookup():
    bridge = VoiceBridge()
    bridge.bind_chunk("c-1", text="Hola, ¿cómo estás?", voice_id="es_ES/davefx", trace_id="t-1")
    bridge.bind_chunk("c-2", text="Bien, gracias.", voice_id="es_ES/davefx", trace_id="t-1")

    assert bridge.get("c-1")["text"] == "Hola, ¿cómo estás?"
    assert bridge.text_of("c-2") == "Bien, gracias."
    assert bridge.chunks_for_trace("t-1") == ["c-1", "c-2"]


def test_voice_bridge_clear():
    bridge = VoiceBridge()
    bridge.bind_chunk("c-1", text="x", voice_id="v")
    bridge.clear()
    assert bridge.get("c-1") is None
    assert bridge.all_chunks() == []


def test_voice_bridge_get_returns_none_for_unknown_chunk():
    bridge = VoiceBridge()
    assert bridge.get("nope") is None
    assert bridge.text_of("nope") is None


def test_voice_bridge_singleton():
    """get_voice_bridge returns the same instance."""
    from server.shared.events.voice_bridge import get_voice_bridge
    a = get_voice_bridge()
    b = get_voice_bridge()
    assert a is b
