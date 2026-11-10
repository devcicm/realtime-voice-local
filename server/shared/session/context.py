"""SessionContext — ambient state for the LLM, never in message history.

This holds transient information that is useful for the model to
know (VAD activity, recent interruptions, audio queue size) but
should NOT be part of the conversation history. The class exposes
two read paths:

  - to_system_context()  →  a string prepended to the system prompt
  - to_json()            →  for the SessionState endpoint and the UI

Defensive: when no state has been touched (fresh session), the
to_system_context() returns an empty string. The ConversationLog
decides whether to prepend the system prompt with this block.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Optional


class SessionContext:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._vad_active: bool = False
        self._user_speaking: bool = False
        self._turn: str = "idle"            # mirrors the TurnStateMachine
        self._transport: str = "websocket"
        self._audio_queue_size: int = 0
        self._audio_queue_bytes: int = 0
        self._audio_last_chunk_at: Optional[float] = None
        self._stt_partial: str = ""
        self._last_interruption: Optional[dict] = None  # {reason, ts, partial}

    # ---- setters ----
    def set_vad_active(self, v: bool) -> None:
        with self._lock:
            self._vad_active = bool(v)

    def set_user_speaking(self, v: bool) -> None:
        with self._lock:
            self._user_speaking = bool(v)

    def set_turn(self, turn: str) -> None:
        with self._lock:
            self._turn = turn

    def set_transport(self, transport: str) -> None:
        with self._lock:
            self._transport = transport

    def set_audio_queue(self, size: int, bytes_count: int, last_chunk_at: Optional[float] = None) -> None:
        with self._lock:
            self._audio_queue_size = int(size)
            self._audio_queue_bytes = int(bytes_count)
            self._audio_last_chunk_at = last_chunk_at

    def set_stt_partial(self, text: str) -> None:
        with self._lock:
            self._stt_partial = text or ""

    def set_interruption(self, reason: str, partial: str = "") -> None:
        with self._lock:
            self._last_interruption = {
                "reason": reason,
                "ts": time.time() * 1000,
                "partial": partial or "",
            }

    def clear_interruption(self) -> None:
        with self._lock:
            self._last_interruption = None

    def reset(self) -> None:
        with self._lock:
            self._vad_active = False
            self._user_speaking = False
            self._turn = "idle"
            self._stt_partial = ""
            self._audio_queue_size = 0
            self._audio_queue_bytes = 0
            self._last_interruption = None

    # ---- reads ----
    def is_empty(self) -> bool:
        """True if no ambient state has been set; no system_context emitted."""
        with self._lock:
            return (
                not self._vad_active
                and not self._user_speaking
                and self._turn == "idle"
                and not self._stt_partial
                and self._audio_queue_size == 0
                and self._audio_queue_bytes == 0
                and self._last_interruption is None
            )

    def to_system_context(self) -> str:
        """Build a compact ambient-state block. Empty when no state is set.

        Format::
            [Session context]
            - User is currently speaking
            - Last interruption: 2.3s ago (barge-in)
            - Turn: MODEL_SPEAKING
            ...
            [/Session context]
        """
        with self._lock:
            if self.is_empty():
                return ""
            lines = ["[Session context]"]
            if self._vad_active:
                lines.append("- VAD: user is currently speaking")
            elif self._user_speaking:
                lines.append("- User is currently speaking (pre-VAD)")
            if self._turn and self._turn != "idle":
                lines.append(f"- Turn: {self._turn}")
            if self._stt_partial:
                preview = self._stt_partial[:60]
                lines.append(f"- STT partial: \"{preview}{'...' if len(self._stt_partial) > 60 else ''}\"")
            if self._audio_queue_size > 0:
                lines.append(f"- Audio queue: {self._audio_queue_size} chunks, {self._audio_queue_bytes} bytes")
            if self._last_interruption is not None:
                ts = self._last_interruption["ts"]
                age_s = (time.time() * 1000 - ts) / 1000
                if age_s < 60:  # only show recent interruptions
                    reason = self._last_interruption["reason"]
                    lines.append(f"- Last interruption: {age_s:.1f}s ago ({reason})")
            if self._transport:
                lines.append(f"- Transport: {self._transport}")
            lines.append("[/Session context]")
            return "\n".join(lines)

    def to_json(self) -> dict:
        with self._lock:
            return {
                "vad_active": self._vad_active,
                "user_speaking": self._user_speaking,
                "turn": self._turn,
                "transport": self._transport,
                "audio_queue": {
                    "size": self._audio_queue_size,
                    "bytes": self._audio_queue_bytes,
                    "last_chunk_at": self._audio_last_chunk_at,
                },
                "stt_partial": self._stt_partial,
                "last_interruption": self._last_interruption,
            }


_singleton: Optional[SessionContext] = None
_lock = threading.Lock()


def get_session_context() -> SessionContext:
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = SessionContext()
        return _singleton


def reset_session_context() -> None:
    global _singleton
    with _lock:
        _singleton = None
