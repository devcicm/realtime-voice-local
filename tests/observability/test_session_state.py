"""Tests for SessionState."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.shared.session import (
    SessionState, get_session_state, reset_session_state,
)


@pytest.fixture
def fresh():
    reset_session_state()
    yield
    reset_session_state()


def test_default_state(fresh):
    s = get_session_state()
    assert s.connection == "idle"
    assert s.turn == "idle"
    assert s.pipeline == "ready"


def test_update_calls_observers(fresh):
    s = get_session_state()
    seen = []
    s.on_change(lambda state: seen.append(state.connection))
    s.update(connection="connecting")
    s.update(connection="open")
    assert seen == ["connecting", "open"]


def test_update_partial(fresh):
    s = get_session_state()
    s.update(connection="open", backend="piper")
    assert s.connection == "open"
    assert s.backend == "piper"
    assert s.turn == "idle"  # unchanged


def test_observer_unsubscribe(fresh):
    s = get_session_state()
    seen = []
    unsub = s.on_change(lambda state: seen.append(state.connection))
    s.update(connection="connecting")
    unsub()
    s.update(connection="open")
    assert seen == ["connecting"]


def test_observer_error_does_not_break_others(fresh):
    s = get_session_state()
    def bad(_): raise RuntimeError("oops")
    s.on_change(bad)
    seen = []
    s.on_change(lambda state: seen.append(state.connection))
    s.update(connection="open")
    assert seen == ["open"]


def test_to_dict_shape(fresh):
    s = get_session_state()
    s.update(session_id="sess-1", voice_id="ef_dora")
    d = s.to_dict()
    assert d["session_id"] == "sess-1"
    assert d["voice_id"] == "ef_dora"
    assert d["connection"] == "idle"
    assert "audio" in d
    assert "capabilities" in d


def test_singleton(fresh):
    a = get_session_state()
    b = get_session_state()
    assert a is b


def test_session_state_endpoint_in_stt():
    """The /session/state endpoint returns the current state."""
    from server.stt.stt_server import app
    client = TestClient(app)
    res = client.get("/session/state")
    assert res.status_code == 200
    body = res.json()
    assert "connection" in body
    assert "turn" in body
    assert "pipeline" in body


def test_session_state_endpoint_in_tts():
    """The /session/state endpoint is also available on the TTS server."""
    from server.tts.websocket_server import app
    client = TestClient(app)
    res = client.get("/session/state")
    assert res.status_code == 200
    body = res.json()
    assert "connection" in body
