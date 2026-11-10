# Observability

> Status: Phases 0-9 complete. 130+ tests passing.

The realtime server stack ships with first-class observability: every
component emits structured events, spans, and metrics through a shared
catalog. Both servers expose a `/metrics` and `/session/state` endpoint;
the client-side mirrors all the primitives so the browser can be a
first-class participant.

## Three pillars

| Pillar      | What                                  | Where                                  |
|-------------|---------------------------------------|----------------------------------------|
| **Tracing** | Causal request flow, span tree        | `server.shared.observability.tracer`   |
| **Metrics** | Counters, Gauges, Histograms          | `server.shared.observability.metrics`  |
| **Events**  | Domain events (LLM tokens, TTS chunks)| `server.shared.events`                 |

Plus:

- **State machines** (4 FSMs: Connection, Turn, Pipeline, Message)
- **Result pattern** (functional `Result<T, E>` for explicit error paths)
- **Event sourcing** (append-only store with replay, 5 backends)
- **Voice/Chat correlation** (cross-channel via `correlation_id`)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       CLIENT (browser)                      │
│  ┌──────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Tracer       │  │ Metrics     │  │ EventStore          │  │
│  │ (in-memory)  │  │ Registry    │  │ (memory strategy)   │  │
│  └──────────────┘  └─────────────┘  └─────────────────────┘  │
│         │                  │                    │            │
│         ▼                  ▼                    ▼            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  VoiceChannel  +  ChatChannel  +  VoiceBridge          │  │
│  │  (EventBus sub-channels)                               │  │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │  X-Trace-Id / X-Span-Id
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       SERVERS (Python)                       │
│  TraceMiddleware  →  Counter/Gauge/Histogram  →  /metrics     │
│         │                    │                       │       │
│         ▼                    ▼                       ▼       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  EventStore (sqlite | memory | jsonl | postgres | mssql)  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                              │                                │
│                              ▼                                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Exporters:  Memory │ JSONL │ Stdout │ OTLP/HTTP         │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Quick start

### 1. Servers already include the middleware

```python
# server/tts/websocket_server.py
app = FastAPI(...)
app.add_middleware(TraceMiddleware)   # <- traces every request
```

### 2. Hit `/metrics` to inspect

```bash
curl http://localhost:8100/metrics | jq
```

Returns:

```json
{
  "metrics": [
    {"type": "counter", "name": "stt.transcribe.requests", "labels": {"model": "tiny"}, "value": 17},
    {"type": "histogram", "name": "stt.transcribe.elapsed_ms", "labels": {"model": "tiny"},
     "buckets": ["10", "25", "50", "100", "250", "500", "1000", "2500", "5000", "10000", "+Inf"],
     "counts": [...], "sum": 4321.5, "count": 17}
  ],
  "recent_traces": [
    {"trace_id": "abc...", "name": "http POST /transcribe", "duration_ms": 240, ...}
  ]
}
```

### 3. Trace propagation is automatic

```bash
curl -H "X-Trace-Id: my-trace-123" -H "X-Span-Id: parent-span" \
     http://localhost:8100/transcribe
# The server echoes the trace_id back; logs and spans tag with it.
```

## Event catalog

The single source of truth is `shared/events.json` (raíz del repo). Every event has a
dotted name, a category, and a documented payload.

**Domain** — lifecycle of a user turn:

| Event | Payload |
|---|---|
| `user.input.received` | `{text, source: 'text'|'stt'}` |
| `stt.transcribed` | `{text, lang, duration_ms, rtf}` |
| `llm.token.delta` | `{text, parent_msg_id, token_idx}` |
| `llm.response.done` | `{parent_msg_id, total_tokens, duration_ms}` |
| `tts.chunk.sent` | `{chunk_id, text, voice_id}` |
| `tts.audio.delta` | `{chunk_id, seq, bytes, duration_ms}` |
| `tts.audio.end` | `{chunk_id, total_bytes, duration_ms, word_offsets_ms}` |
| `audio.played` | `{chunk_id, samples_played, duration_ms}` |
| `transcript.delta` | `{chunk_id, word, char_start, char_end}` |
| `message.displayed` | `{msg_id, role, char_count}` |

**Communication** — connection lifecycle:

`conn.opening`, `conn.open`, `conn.closing`, `conn.closed`, `conn.failed`, `conn.reconnecting`, `conn.reconnected`

**Voice activity** — VAD:

`vad.speech.start`, `vad.speech.end`, `vad.turn.complete`

**Control** — user-driven:

`interrupt.requested`, `interrupt.ack`, `mode.changed`, `config.updated`

**Diagnostic**:

`error.raised`, `latency.observed`, `circuit_breaker.changed`, `state.changed`

### Publishing from code

```python
from server.shared.events import VoiceChannel, ChatChannel, EventType

# Server-side: emit a TTS audio delta
VoiceChannel.emit(EventType.TTS_AUDIO_DELTA, {
    "chunk_id": "c-1",
    "seq": 1,
    "bytes": 4096,
    "duration_ms": 170,
}, correlation_id=trace_id)

# Client-side: emit a chat token
ChatChannel.emit(EventType.LLM_TOKEN_DELTA, {
    "text": "Hola",
    "parent_msg_id": msg.id,
    "token_idx": 1,
}, correlation_id=trace_id)
```

### Subscribing

```python
from server.shared.events import VoiceChannel, EventType

# One-shot subscription
unsub = VoiceChannel.subscribe(EventType.TTS_AUDIO_END, lambda e: print(e.payload))
# ...
unsub()

# Subscribe to everything (careful — high volume)
VoiceChannel.subscribe_all(lambda e: log(e))
```

## State machines

Four FSMs are pre-built. Each emits `state.changed` events on transition.

```python
from server.shared.state import (
    ConnectionStateMachine,
    TurnStateMachine,
    PipelineStateMachine,
    MessageStateMachine,
)

m = ConnectionStateMachine()
m.transition("CONNECTING", reason="user-initiated")
m.transition("OPEN")
m.snapshot()
# {'name': 'ConnectionStateMachine', 'current': 'OPEN',
#  'history': [{...}, {'from': 'IDLE', 'to': 'CONNECTING', ...}, ...]}
```

| FSM        | States                                                              |
|------------|---------------------------------------------------------------------|
| Connection | IDLE → CONNECTING → OPEN → CLOSING → CLOSED, plus ERROR              |
| Turn       | IDLE → USER_SPEAKING → USER_DONE → MODEL_THINKING → MODEL_SPEAKING |
| Pipeline   | READY ↔ STREAMING; FAILED, INTERRUPTED can be reset to READY        |
| Message    | CREATED → QUEUED → STT → LLM → SPLIT → TTS → PLAYING → COMPLETED    |

Invalid transitions raise `InvalidTransitionError`. Use `force()` to
bypass the table (e.g. for crash recovery).

## Result pattern

`Result<T, E>` makes errors values, not exceptions. Use for expected
error paths; keep `raise` for truly exceptional cases.

```python
from server.shared.result import Result, of, of_async

# Sync
res = of(lambda: int("abc"))
if res.is_err():
    return Response(status_code=400, body=str(res.error))
value = res.unwrap()

# Async
res = await of_async(some_coro())
res.map(lambda v: transform(v))
   .flat_map(lambda v: of_async(next_step(v)))
```

## Event sourcing

Append-only log of all events, with replay capability.

### Strategies

| Strategy     | Backend                                  | Use                              |
|--------------|------------------------------------------|----------------------------------|
| `memory`     | In-process dict                          | Tests, ephemeral                  |
| `sqlite`     | SQLite file (default)                    | Single-process servers, dev      |
| `jsonl`      | Append-only JSONL file                   | Backup, export                   |
| `postgres`   | PostgreSQL                               | Production multi-instance       |
| `sqlserver`  | Microsoft SQL Server                     | Enterprise Microsoft stack       |

### Usage

```python
from server.shared.events import EventStore, create_store

# Pick backend via env
store = create_store("sqlite", db_path="data/events.db")
# or
store = EventStore.memory()

await store.append({
    "id": "evt-1",
    "type": "user.input.received",
    "source": "input",
    "ts": "2026-07-25T12:00:00Z",
    "ts_ms": 1721904000000.0,
    "payload": {"text": "Hello"},
    "correlation_id": "trace-abc",
    "trace": {"trace_id": "trace-abc", "span_id": "span-1"},
})

# Query
events = await store.get_by_correlation_id("trace-abc")
trace_events = await store.get_by_trace_id("trace-abc")
recent = await store.get_by_type("tts.audio.delta", limit=100)
new_events = await store.get_since(seq=last_known_seq)
```

### Crash recovery

```python
from server.shared.events import replay_from

# On startup:
#   1. Read the latest snapshot (state at seq N).
#   2. Read all events with seq > N.
#   3. Fold them with reducers to reconstruct current state.

reducers = {
    "tts.audio.delta": lambda s, e: {**s, "bytes_played": s.get("bytes_played", 0) + e["payload"]["bytes"]},
    "llm.token.delta": lambda s, e: {**s, "tokens": s.get("tokens", 0) + 1},
}
state = await replay_from(store, since_seq=N, reducers=reducers, initial_state={})
```

## OTLP / OpenTelemetry compatibility

`OtlpHttpExporter` posts snapshots to an OTLP/HTTP endpoint
(`http://host:port/v1/metrics`). Compatible with the OTel Collector,
Tempo, Mimir, Honeycomb, etc.

```python
from server.shared.observability import (
    get_registry, Flusher, OtlpHttpExporter,
)

flusher = Flusher(
    get_registry(),
    [OtlpHttpExporter("http://otel-collector:4318/v1/metrics")],
    interval_ms=10_000,
)
flusher.start()
```

## Configuration

```env
# .env
METRICS_BACKEND=memory,jsonl,otlp
METRICS_FLUSH_INTERVAL_MS=5000

# Event store
EVENT_STORE_BACKEND=sqlite
EVENT_STORE_PATH=./data/events.db
# EVENT_STORE_BACKEND=postgres
# EVENT_STORE_DSN=postgresql://user:pass@host:5432/events
# EVENT_STORE_BACKEND=sqlserver
# EVENT_STORE_DSN=mssql+pyodbc://user:pass@host/events?driver=ODBC+Driver+17+for+SQL+Server
EVENT_STORE_SNAPSHOT_INTERVAL=100
EVENT_STORE_MAX_RING=50000

# OTLP
OTLP_ENDPOINT=http://localhost:4318
OTLP_SERVICE_NAME=kokoro-tts
```

## Tests

```bash
pytest tests/observability/   # 50+ tests for tracer, metrics, exporters, OTLP, FSM
pytest tests/events/          # 40+ tests for event bus, store, replay
pytest tests/state/          # 25+ tests for state machines
pytest tests/result/          # 10+ tests for Result pattern
```

Total: **130+ tests** across the observability layer.

## See also

- [OpenAI Realtime API](https://platform.openai.com/docs/guides/realtime) — the architecture we mirror
- [OpenTelemetry Metrics](https://opentelemetry.io/docs/specs/otel/metrics/) — the data model we emit
- `shared/events.json` — the canonical event catalog
- `docs/MANUAL.md` — top-level system documentation
- `docs/architecture/e2e-voice-flow.html` — diagramas E2E actualizados (build d)
