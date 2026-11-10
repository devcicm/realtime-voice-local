"""Events facade — single import for event types, bus, channels, store."""
from .types import EventType, EVENT_TYPES, is_known
from .envelope import Event
from .bus import (
    EventBus, Channel,
    VoiceChannel, ChatChannel, EventBusInstance,
    is_known_type,
)
from .voice_bridge import VoiceBridge, get_voice_bridge
from .store import (
    EventStore,
    EventRepository,
    Snapshot,
    create_store,
)
from .store.strategies.memory import MemoryStrategy
from .store.strategies.sqlite import SqliteStrategy
from .store.strategies.jsonl import JsonlStrategy
from .recovery import replay_from, get_snapshot_at

__all__ = [
    "EventType", "EVENT_TYPES", "is_known",
    "Event", "EventBus", "Channel",
    "VoiceChannel", "ChatChannel", "EventBusInstance",
    "is_known_type",
    "VoiceBridge", "get_voice_bridge",
    "EventStore", "EventRepository", "Snapshot", "create_store",
    "MemoryStrategy", "SqliteStrategy", "JsonlStrategy",
    "replay_from", "get_snapshot_at",
]
