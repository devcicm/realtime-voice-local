"""EventBus — pub/sub with envelope + per-channel namespacing.

Two pre-built channels live at the module level so producer and
consumer code can import them without circular deps:

  - VoiceChannel  — VAD, audio capture, TTS playback, transcripts
  - ChatChannel   — text input, LLM tokens, message display

Channels are independent; cross-channel correlation is done via
``correlation_id`` (typically a trace_id). A subscriber can listen
to *both* channels to reconstruct a full conversation.

Mirrors ``client/src/events/bus.js``.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Iterable

from .envelope import Event
from .types import EventType

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[Callable]] = defaultdict(set)
        self._all_subscribers: set[Callable] = set()
        self._lock = False  # naive re-entrancy guard

    # ---- subscribe ----

    def subscribe(self, type_: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers[type_].add(handler)

        def unsubscribe() -> None:
            self._subscribers[type_].discard(handler)

        return unsubscribe

    def subscribe_all(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        self._all_subscribers.add(handler)

        def unsubscribe() -> None:
            self._all_subscribers.discard(handler)

        return unsubscribe

    def subscribers(self, type_: str) -> Iterable[Callable]:
        return tuple(self._subscribers[type_])

    # ---- publish ----

    def publish(self, event: Event) -> Event:
        for handler in self._all_subscribers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                logger.exception("subscriber-all error type=%s", event.type)
        for handler in self._subscribers.get(event.type, ()):
            try:
                handler(event)
            except Exception:  # noqa: BLE001
                logger.exception("subscriber error type=%s", event.type)
        return event

    def emit(
        self,
        type_: str,
        payload: dict | None = None,
        *,
        source: str = "unknown",
        correlation_id: str | None = None,
        causation_id: str | None = None,
        trace: dict | None = None,
    ) -> Event:
        evt = Event(
            type=type_,
            source=source,
            payload=dict(payload or {}),
            correlation_id=correlation_id,
            causation_id=causation_id,
            trace=trace,
        )
        return self.publish(evt)


class Channel:
    """Namespaced view over a shared EventBus.

    Producers use ``channel.emit(...)`` and consumers use
    ``channel.subscribe(event_type, handler)``. Subscribers are
    scoped to the channel — the same EventType can have different
    handlers on VoiceChannel vs ChatChannel.
    """

    def __init__(self, name: str, bus: EventBus) -> None:
        self.name = name
        self._bus = bus

    def emit(self, type_: str, payload: dict | None = None, **kwargs) -> Event:
        kwargs.setdefault("source", self.name)
        return self._bus.emit(type_, payload, **kwargs)

    def subscribe(self, type_: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        return self._bus.subscribe(type_, handler)

    def subscribe_all(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        return self._bus.subscribe_all(handler)


# Shared bus + two channels. Importing this module gives the same
# instance everywhere, so producers and consumers connect automatically.
_shared_bus = EventBus()
VoiceChannel = Channel("voice", _shared_bus)
ChatChannel = Channel("chat", _shared_bus)
EventBusInstance = _shared_bus  # for tests that want raw access


def is_known_type(value: str) -> bool:
    from .types import is_known
    return is_known(value)


__all__ = [
    "Event", "EventBus", "Channel",
    "VoiceChannel", "ChatChannel", "EventBusInstance",
    "EventType",
    "is_known_type",
]
