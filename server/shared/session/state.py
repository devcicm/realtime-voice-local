"""SessionState — reactive snapshot of the realtime session.

The state object is updated by the connection/turn/pipeline FSMs and
read by the UI inspector. Updates go through ``update()`` which fires
observers.

Mirrors ``client/src/session/state.js``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


@dataclass
class SessionState:
    session_id: str = ""
    connection: str = "idle"  # idle | connecting | open | closing | closed | error
    transport: str = "websocket"  # websocket | webrtc | http-stream
    backend: str = ""  # piper | xtts | kokoro | openai-realtime
    turn: str = "idle"  # idle | user_speaking | user_done | model_thinking | model_speaking | interrupted
    pipeline: str = "ready"  # ready | streaming | failed | interrupted
    llm_provider: str = "lmstudio"  # lmstudio | ollama | openai | anthropic
    llm_model: str = ""
    voice_id: str = ""
    capabilities: dict = field(default_factory=dict)
    rate_limits: dict = field(default_factory=dict)
    audio: dict = field(default_factory=lambda: {
        "direction": "none",
        "sample_rate": 24000,
        "queue_size": 0,
        "queue_bytes": 0,
    })
    counters: dict = field(default_factory=dict)

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._notify()

    def to_dict(self) -> dict:
        return asdict(self)

    # ---- observers ----

    def on_change(self, callback: Callable[["SessionState"], None]) -> Callable[[], None]:
        self._observers.add(callback)
        return lambda: self._observers.discard(callback)

    def _notify(self) -> None:
        for obs in getattr(self, "_observers", []):
            try:
                obs(self)
            except Exception:
                pass


_singleton: Optional[SessionState] = None
_lock = threading.Lock()


def get_session_state() -> SessionState:
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = SessionState()
            _singleton._observers = set()
        return _singleton


def reset_session_state() -> None:
    global _singleton
    with _lock:
        _singleton = None
