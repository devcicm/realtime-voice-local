"""VoiceBridge — joins voice-channel events to chat-channel events.

Stores the binding ``chunk_id -> {trace_id, text, voice_id, sent_at}`` so
a consumer can subscribe to audio chunks and ask "what text is this
chunk speaking?".

Mirrors ``client/src/events/voiceBridge.js``.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class VoiceBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_chunk: dict[str, dict] = {}
        self._by_trace: dict[str, list[str]] = {}

    def bind_chunk(self, chunk_id: str, *, text: str, voice_id: str, trace_id: Optional[str] = None) -> None:
        with self._lock:
            self._by_chunk[chunk_id] = {
                "text": text,
                "voice_id": voice_id,
                "trace_id": trace_id,
                "sent_at_ms": time.time() * 1000,
            }
            if trace_id:
                self._by_trace.setdefault(trace_id, []).append(chunk_id)

    def get(self, chunk_id: str) -> Optional[dict]:
        with self._lock:
            return self._by_chunk.get(chunk_id)

    def chunks_for_trace(self, trace_id: str) -> list[str]:
        with self._lock:
            return list(self._by_trace.get(trace_id, ()))

    def text_of(self, chunk_id: str) -> Optional[str]:
        return (self.get(chunk_id) or {}).get("text")

    def all_chunks(self) -> list[dict]:
        with self._lock:
            return [{"chunk_id": cid, **data} for cid, data in self._by_chunk.items()]

    def clear(self) -> None:
        with self._lock:
            self._by_chunk.clear()
            self._by_trace.clear()


_singleton: VoiceBridge | None = None


def get_voice_bridge() -> VoiceBridge:
    global _singleton
    if _singleton is None:
        _singleton = VoiceBridge()
    return _singleton
