# Harness Chat Voice — Documentación Final

Harness conversacional con captura de voz, STT (Parakeet / faster-whisper), LLM (LM Studio), TTS (Piper), trazabilidad end-to-end, y arquitectura de mensajería basada en Pub/Sub.

> Arquitectura completa con diagramas de flujo y de estados: [ARCHITECTURE.md](ARCHITECTURE.md). Voz en vivo por un solo WebSocket: [REALTIME.md](REALTIME.md).

## Indice

1. [Arquitectura general](#arquitectura-general)
2. [Servicios](#servicios)
3. [Instalacion y arranque](#instalacion-y-arranque)
4. [Arquitectura del cliente](#arquitectura-del-cliente)
5. [Patrones aplicados](#patrones-aplicados)
6. [Trazabilidad y observabilidad](#trazabilidad-y-observabilidad)
7. [Manejo de errores y resiliencia](#manejo-de-errores-y-resiliencia)
8. [Eventos del sistema](#eventos-del-sistema)
9. [Audio pipeline (mic + DSP + STT)](#audio-pipeline-mic--dsp--stt)
10. [TTS pipeline (sentence splitter + sanitizer)](#tts-pipeline-sentence-splitter--sanitizer)
11. [UI completa y controles](#ui-completa-y-controles)
12. [Performance esperado](#performance-esperado)
13. [Troubleshooting](#troubleshooting)
14. [Limitaciones conocidas](#limitaciones-conocidas)
15. [Roadmap](#roadmap)
16. [Archivos del proyecto](#archivos-del-proyecto)

---

## Arquitectura general

```
+--------------------+    POST /transcribe    +-----------------+
| Browser (cliente)  |  ------------------->  | STT microservice|
| - getUserMedia     |    (WAV 16kHz PCM)     | (faster-whisper)|
| - VAD (RMS)        |                        | puerto 8100     |
| - Web Audio        |                        +-----------------+
| - State Machine    |    POST /llm/v1/chat/completions
| - Circuit Breaker  |  ------------------->  +-----------------+
| - Message Bus      |    (OpenAI streaming)  | LLM proxy       |
| - Pipeline         |    via FastAPI :8990   | (websocket_server)
| - Tracer           |        └─────────────> |   LM Studio     |
| - Event Manager    |    WS /ws/tts          |   :12344        |
| - Audio DSP        |  <-------------------  +-----------------+
+--------------------+    (PCM chunks)        | TTS             |
                                              | Piper :8990     |
                                              +-----------------+
```

3 servicios independientes + 1 cliente browser + el proxy LLM embebido en el mismo proceso FastAPI (8990). Cero acoplamiento entre capas.

> **Nota sobre el LLM:** el browser **no** habla directo con LM Studio. Todo request pasa por el proxy `/llm/{path}` del `websocket_server.py` (puerto 8990), que reenvía a LM Studio (`:12344`) en streaming. Esto permite trazabilidad, cancelación upstream y un único punto de entrada al LLM.

## Servicios

| Servicio | Puerto | URL | Stack | Archivo |
|----------|--------|-----|-------|---------|
| STT | 8100 | http://localhost:8100 | FastAPI + Parakeet (defecto) / faster-whisper | `server/stt/stt_server.py` |
| LLM (proxy) | 8990 | http://localhost:8990/llm/v1 | FastAPI proxy → LM Studio :12344 | `server/tts/websocket_server.py` |
| TTS | 8990 | ws://localhost:8990/ws/tts · POST http://localhost:8990/tts | FastAPI + Piper | `server/tts/websocket_server.py` |
| Realtime | 8990 | ws://localhost:8990/ws/voice | VAD + STT + LLM + TTS en el servidor | `server/realtime/` |
| Estático (cliente) | 5500 | http://localhost:5500/client/client_example.html | http.server sin cache | `serve_nocache.py` |
| LM Studio | 12344 | http://localhost:12344/v1 | externo (OpenAI-compat) | — |

---

## Instalacion y arranque

### 1. Dependencias

```bash
# STT server
pip install -r server/stt/requirements-stt.txt

# TTS server
pip install -r server/tts/requirements-tts.txt
```

### 2. Arrancar servicios

Lo normal es `start.cmd` (o `python program.py` desde el IDE), que levanta los tres procesos. A mano:

```bash
# Terminal 1: STT (modelo por defecto: STT_MODEL=parakeet)
python server/stt/stt_server.py --port 8100

# Terminal 2: TTS (WebSocket + proxy /llm embebido)
python server/tts/websocket_server.py

# Terminal 3: servidor estático sin cache
python serve_nocache.py 5500

# Terminal 4: LM Studio (externo, abrir app + cargar modelo)
```

### 3. Abrir el cliente

```bash
# Abre: http://localhost:5500/client/client_example.html
```

> **IMPORTANTE**: usar `serve_nocache.py` (no `python -m http.server`): manda `Cache-Control: no-cache` y neutraliza los 304, garantizando que los cambios en `client_example.html` lleguen al tab con cada Ctrl+F5.

---

## Arquitectura del cliente

El cliente (`client_example.html`) implementa una **arquitectura de mensajeria lite** con los siguientes componentes:

### Diagrama interno

```
                        ┌─────────────────────────────────┐
[User input / STT]      │       MessageBus (pub/sub)     │
        ↓               │  ┌──────────────────────────┐  │
[Producer]              │  │ Subscribers:             │  │
        ↓               │  │  - USER_TEXT handler    │  │
[MessageBus.emit()]────→│  │  - LLM_TOKEN handler    │  │
                        │  │  - LLM_END handler      │  │
                        │  │  - TTS_REQUEST handler  │  │
                        │  │  - INTERRUPT handler    │  │
                        │  └──────────────────────────┘  │
                        └─────────────────────────────────┘
                                    ↓
                        ┌─────────────────────────────────┐
                        │  Pipeline de stages:           │
                        │  [STT] → [LLM] → [SPLIT] → [TTS]│
                        └─────────────────────────────────┘
                                    ↓
                        [TTS WebSocket] → [Audio Playback]
```

### Componentes principales

| Componente | Responsabilidad |
|------------|----------------|
| `ConversationState` | State machine global de la conversacion (IDLE, RECORDING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, ERROR) |
| `Message` | Unidad basica con id, type, payload, state, history, timestamps |
| `MessageBus` | Pub/Sub central con routing por tipo, metricas, ring buffer |
| `MessageStateMachine` | Transiciones validas del state de un mensaje (tabla) |
| `Pipeline` | Encadena stages enchufables (STT -> LLM -> SPLIT -> TTS) |
| `STTStage` / `LLMStage` / `SplitStage` / `TTSStage` | Stages del pipeline |
| `CircuitBreaker` | 3 estados (CLOSED/OPEN/HALF_OPEN) con backoff para LLM |
| `Tracer` | Tracing con trace_id unico por interaccion, eventos, metricas |
| `EventManager` | Captura eventos del DOM/WS/Network y los enruta al bus |
| `AudioDenoiser` | Pipeline DSP browser-side: highpass, lowpass, preemph, gate, spectral |
| `AudioCapture` | Captura de mic con getUserMedia, VAD por RMS, denoising, resiliencia |
| `SentenceSplitter` | Divide respuesta LLM en chunks TTS-friendly (por clausulas) |
| `Sanitizer` | Limpia markdown y emojis del texto que va al TTS |
| `ReasoningDetector` | Heuristica para detectar razonamiento infiltrado en content |

---

## Patrones aplicados

| Patron | Donde | Beneficio |
|--------|-------|-----------|
| **Pub/Sub** | `MessageBus.subscribe(type, handler)` | Desacopla productores de consumidores |
| **Command** | `Message` como command tipado | Cada accion es un mensaje inmutable |
| **State Machine** | `Message.transition()` con tabla de transiciones validas | Estado aislado por mensaje, sin race conditions |
| **Strategy** | Multi-backend TTS (Kokoro/Piper/XTTS) via `TTSBackend` interface | Cambiar backend sin tocar callers |
| **Middleware Chain** | `Pipeline.run()` con stages enchufables | Agregar/quitar stages sin reescribir |
| **Producer-Consumer** | LLMStage produce tokens, SplitStage consume y produce TTS_REQUEST | Streaming con backpressure natural |
| **Singleton** | `globalThis.MessageBus`, `Tracer$` | Una sola instancia compartida |
| **Observer** | `subscribeAll()` para UI updates | Multiples UIs pueden reaccionar |
| **Circuit Breaker** | `LLMStage.abort()` cuando llega INTERRUPT | Falla rapida cuando el LLM no responde |
| **Backpressure** | `SplitStage.buffers` con capacidad acotada | No inunda el TTS |
| **Chain of Responsibility** | `Pipeline` itera stages con try/catch | Errores se aislan en el stage que fallo |
| **Repository** | `MessageBus.messages` map con cleanup | Inspección y replay |
| **Decorator** | Cada stage decora el mensaje y lo pasa al siguiente | Composicion flexible |
| **Template Method** | `PipelineStage.process()` es el template, subclases implementan | Estructura comun + customizacion |
| **Null Object** | Filtros DSP cuando `level='off'` retornan el input sin tocar | Codigo limpio sin ifs anidados |
| **Facade** | `MessageBus.emit()` es un facade de `dispatch(new Message())` | API simple para casos comunes |
| **Memento** | `Message.history` guarda todas las transiciones | Time-travel debugging |
| **Mediator** | `MessageBus` media entre producers y consumers | Desacoplamiento total |

---

## Trazabilidad y observabilidad

### Tracer (por interaccion del usuario)

Cada vez que el usuario interactua, se genera un `trace_id` (UUID) que se propaga a TODO el pipeline:

```
[trace:a1b2c3d4] [input] "Mensaje: Hola, ¿cómo estás?"
[trace:a1b2c3d4] [llm-ttft] 3456ms
[trace:a1b2c3d4] [tts-flush] chunk #1 enviado
[trace:a1b2c3d4] [tts-flush] chunk #2 enviado
[trace:a1b2c3d4] [llm] stream completo: 87 tokens
[trace:a1b2c3d4] finish: success, tts_chunks=3
```

### Metricas

Card "🔍 Trazas (observabilidad)" muestra en tiempo real:
- **total**: total de trazas
- **exito**: trazas completadas OK
- **tasa**: % de exito
- **avg ms**: latencia promedio (rolling 50)
- **p95 ms**: percentil 95 de latencia

### UI de Trazas

Cada traza muestra:
- ID corto (8 chars) + categoria (💬 chat, 🎙️ stt, 🔊 voice_select)
- Metadata (model, voice, audioSize)
- Timeline de eventos con `+Xms` desde el evento anterior
- Status final (success/failed)

### Log estructurado

```js
log("mensaje", "info", traceId, "stage")
// Output: [HH:MM:SS] [INFO] [a1b2c3d4] [tts-flush] TTS chunk #1 enviado
```

Cada entry incluye timestamp, nivel, trace_id corto, stage, mensaje.

---

## Manejo de errores y resiliencia

### State machine global (ConversationState)

```
IDLE
 └─ (mic on, user starts talking) → RECORDING
RECORDING
 ├─ (RMS > threshold) → RECORDING (continue)
 └─ (silence > 700ms) → TRANSCRIBING
TRANSCRIBING
 ├─ (200 OK) → THINKING
 └─ (timeout/error) → IDLE (retry fallback to text input)
THINKING (LLM streaming)
 ├─ (token arrives) → SPEAKING (envía chunk al TTS)
 ├─ (barge-in) → INTERRUPTED
 └─ (LLM done) → IDLE
SPEAKING (TTS playing)
 ├─ (audio_end del WS) → IDLE
 ├─ (barge-in) → INTERRUPTED
 └─ (error del WS) → IDLE (reconectar)
INTERRUPTED → IDLE (auto, 100ms)
```

Transiciones invalidas se loguean y se ignoran.

### Circuit Breaker (LLM)

- 3 errores **seguidos** → OPEN (rechaza nuevos requests 30s). Un exito pone el contador a 0.
- A los 30s pasa **solo** a HALF_OPEN (cuenta atras visible: "reintento en Ns") y deja pasar 1 sonda
- Sonda OK → CLOSED; sonda falla → OPEN otros 30s; sonda abortada (barge-in, cancelar) → se libera y la siguiente peticion vuelve a probar
- Barge-in, cancelar y rechazar un `confirm()` **no** cuentan como fallo (`recordAborted()`)
- UI: `Circuit LLM: CLOSED (sano)` (verde), `CLOSED (1/3 fallos seguidos · ultimo: …)` (amarillo), `OPEN — reintento en Ns` (rojo), `HALF_OPEN`
- Clic en el indicador = reinicio manual a CLOSED (`forceReset()`)

Diagrama de estados: [ARCHITECTURE.md §5.2](ARCHITECTURE.md#52-circuit-breaker-del-llm).

### Resiliencia del WebSocket TTS

- Backoff exponencial en reconexion: 2s, 4s, 8s, 16s, max 30s
- 5 reintentos max antes de marcar como "manual"
- Pre-warm del AudioContext en cualquier user click (Chrome autoplay policy)

### Resiliencia del microfono

- Deteccion de `track.ended` (mic desenchufado)
- Deteccion de `track.mute` (mic muteado por el SO)
- Listener de `devicechange` (cambio de hardware)
- Backoff exponencial en reconexion: 1s, 2s, 4s, 8s, 16s, 30s, **sin limite de reintentos**
- **Siempre encendido** (build `20261110`): el mic solo se apaga con clic de raton en el boton o con el comando de voz "apagar" (`AppState.micWanted`). El boton ignora Espacio/Enter para no apagarlo sin querer.
- **Watchdog cada 3s** mientras `micWanted`: recrea la captura si no existe, reconecta si el track murio o si pasan >5s sin bloques de audio, reanuda el `AudioContext` suspendido y devuelve a RECORDING los estados IDLE/ERROR/INTERRUPTED cuando no hay nada en curso
- **Comando de voz**: "apagar", "apágate", "apaga el micrófono, por favor", "detener el micro"… (max 6 palabras; "para" o "apagar la luz de la cocina" no cuentan). Ver [ARCHITECTURE.md §6.1](ARCHITECTURE.md#61-micrófono-siempre-encendido-cliente-clásico)
- **Descarte de ruido**: si la captura necesito ganancia ≥3.4x y tuvo <4 bloques con voz, no se manda al STT
- Distincion de errores:
  - `NotAllowedError` → permiso denegado, NO reintenta
  - `NotFoundError` → no hay mic, reintenta
  - `NotReadableError` → mic ocupado, reintenta
  - `OverconstrainedError` → error de config
  - `AbortError` → cancelado por usuario

### Estados del boton mic (UI)

| Estado | Color | Texto |
|--------|-------|-------|
| `connecting` | (default) | "⏳ Conectando..." |
| `active` | verde pulsante | "⏹️ Detener mic" |
| `muted` | verde (quieto) | "🔇 Mic muteado (sistema)" |
| `denied` | gris | "🚫 Permiso denegado" |
| `no-device` | naranja | "🔌 Sin mic - reintentando..." |
| `busy` | amarillo | "⏸️ Mic ocupado - reintentando..." |
| `retrying` | azul pulsante | "🔄 Reintento 3 en 4s... (motivo)" |
| `lost` | naranja | "⚠️ Reconectando..." |
| `failed` | rojo | "❌ Fallo - click para reintentar" |
| `error` | rojo | "⚠️ Error - click para reintentar" |
| `stopped` | verde (normal) | "🎙️ Activar micrófono" (solo tras apagado manual o por voz) |

---

## Eventos del sistema

### EventManager (DOM, WS, Network)

Captura eventos del browser y los enruta al bus:

**Eventos DOM**: `CLICK`, `KEYDOWN`, `KEYUP`, `INPUT`, `FOCUS`, `BLUR`
**Eventos Window**: `VISIBILITY`, `RESIZE`, `UNLOAD`
**Eventos Network**: `ONLINE`, `OFFLINE`
**Eventos WS**: `WS_OPEN`, `WS_CLOSE`, `WS_ERROR`, `WS_MESSAGE`
**Eventos Mic**: `MIC_GRANTED`, `MIC_DENIED`, `MIC_LOST`, `MIC_RECOVER`

### Card "🎬 Eventos del sistema"

Muestra los ultimos 40 eventos con:
- Timestamp ms-precision
- Categoria con color (dom, ws, window, mic)
- Payload resumido
- Filtros por categoria

### Instrumentacion automatica

Al cargar la pagina se instalan listeners para:
- `click` en document
- `keydown`/`keyup` en document
- `input` en document
- `visibilitychange` en document
- `online`/`offline`/`resize`/`beforeunload` en window

El WebSocket se instrumenta automaticamente con `Events.instrumentWebSocket(ws)`.

---

## Audio pipeline (mic + DSP + STT)

### Flujo de captura

```
[getUserMedia 16kHz mono]
      ↓
[AudioContext + AnalyserNode]
      ↓
[AudioDenoiser] ← (noise profile aprendido en 0.5s)
      ↓
[VAD RMS] → si silencio > 700ms → [descarte de ruido] → [STT]
      ↓
[WAV 16-bit PCM] → POST /transcribe
      ↓
[STT microservice -> Parakeet (o faster-whisper) -> texto]
      ↓
[¿comando "apagar"?] → si: stopMicByUser
      ↓
[MessageBus.emit(USER_TEXT)]
```

### Pipeline DSP (AudioDenoiser)

| Capa | Que hace | Cuando se aplica |
|------|----------|------------------|
| **1. Noise gate** | Si RMS < threshold, atenua 95-98% del audio | medium/high |
| **2. Pre-emphasis** | Realza frecuencias agudas (consonantes) | medium/high |
| **3. Highpass 80Hz** | Elimina rumble/electric hum (<80Hz) | low/medium/high |
| **4. Lowpass 8kHz** | Elimina hiss agudo (>8kHz) | medium/high |
| **5. Spectral subtraction** | Resta ruido estimado del espectro | high |

### Aprendizaje del ruido

En los primeros 0.5s (8000 samples @ 16kHz) el sistema aprende el perfil de ruido de fondo con un EMA. Este perfil se usa en spectral subtraction.

### Niveles de filtrado (UI)

| Nivel | Uso recomendado | Reduccion |
|-------|-----------------|-----------|
| **Off** | Mic de calidad (headset), ambiente silencioso | 0% |
| **Bajo** | Oficina tranquila | ~10-20% (gate) |
| **Medio** ⭐ | Oficina normal, ventiladores, tecleo | ~50% (gate + filtros + preemph) |
| **Alto** | Mucho ruido, mic de laptop | ~70% (+ spectral) |

### Validacion empirica del denoiser

| Test | off | medium | high |
|------|-----|--------|------|
| Ruido puro (50Hz hum + blanco) | 0.0144 RMS | 0.0077 RMS (-47%) | 0.0077 RMS (-47%) |
| Voz 300Hz + ruido | 0.2363 RMS | 0.1150 RMS (-51%) | 0.1150 RMS (-51%) |
| Silencio total | 0.0000 | 0.0000 | 0.0000 |
| 50Hz tone puro | 0.0707 | **0.0189** (-73%) | - |

---

## TTS por HTTP

Además del WebSocket, para pedir audio con una sola petición (scripts, otros servicios, curl) sin abrir un WebSocket:

```bash
curl -X POST http://127.0.0.1:8990/tts -H "Content-Type: application/json" -d "{\"text\":\"Hola, esto es una prueba.\"}" -o hola.wav
```

- Cuerpo JSON: `text` (obligatorio, ≤ 5000 caracteres), `voice` (ver `GET /voices`), `speed` (0.25–4), `format` `wav` (por defecto) o `pcm` (s16le mono crudo), `stream` (`true` = enviar mientras se genera).
- Respuesta: `audio/wav` PCM16 mono, con cabeceras `X-Sample-Rate`, `X-Voice`, `X-Audio-Duration-Ms` y `X-Synthesis-Ms`.
- Errores: 400 (texto vacío, formato o speed inválido), 404 (voz desconocida), 413 (texto largo), 503 (backend sin inicializar).
- `POST /v1/audio/speech` acepta el formato de OpenAI (`input`, `voice`, `speed`, `response_format`), así que los SDK de OpenAI sirven con `base_url=http://127.0.0.1:8990/v1`. Las voces de OpenAI (`alloy`, …) usan la voz actual del servidor.
- Cambiar `voice` reinicializa el backend (global): las peticiones HTTP se serializan entre sí.

**En vivo, sin guardar archivo:** con `"stream": true` el audio se envía a medida que se genera (chunked, primer byte en milisegundos) y se puede reproducir mientras llega:

```bash
curl -sN -X POST http://127.0.0.1:8990/tts -H "Content-Type: application/json" -d "{\"text\":\"Hola en vivo\",\"stream\":true}" | ffplay -nodisp -autoexit -i -
```

Con `format: "pcm"` llega PCM16 mono crudo (frecuencia en la cabecera `X-Sample-Rate`). Si el cliente corta la conexión, la síntesis se detiene.

Reproducir en Windows:

```bash
powershell -c "(New-Object Media.SoundPlayer 'hola.wav').PlaySync()"
```

### Autenticación por API key (clientes remotos)

Antes de exponer el servidor TTS más allá de tu PC, define una o varias claves en `.env` (separadas por comas):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```
API_KEYS=la-clave-generada
```

Con claves, toda petición HTTP y WebSocket del servidor TTS (`/tts`, `/v1/audio/speech`, `/ws/tts`, `/ws/voice`, `/llm/*`, `/voices`…) exige la clave; solo `/health` queda libre. Sin clave válida responde **401** (HTTP) o rechaza el handshake (**403**, WebSocket). Formas de enviarla:

- `Authorization: Bearer <clave>` (recomendada; es la que usan los SDK de OpenAI con `api_key=`)
- `X-API-Key: <clave>`
- `?api_key=<clave>` en la URL, para WebSocket desde navegador (no puede poner cabeceras). Queda en logs de proxies: úsalo solo con `wss://`.

```bash
curl -sN -X POST https://tu-dominio/tts -H "Authorization: Bearer $TTS_API_KEY" -H "Content-Type: application/json" -d "{\"text\":\"Hola\",\"stream\":true}" | ffplay -nodisp -autoexit -i -
```

```bash
python client/tts_ws_client.py "Hola" --url wss://tu-dominio/ws/tts --api-key TU_CLAVE
```

Sin `API_KEYS` todo sigue abierto como antes (uso local). Para Internet, pon además TLS delante (Caddy, nginx o Cloudflare Tunnel) para que la clave y el audio viajen cifrados (`https://` / `wss://`). El cliente del navegador (`client_example.html`) y el STT (:8100) aún no envían ni piden clave.

---

## TTS pipeline (sentence splitter + sanitizer)

### Flujo de salida

```
[LLM tokens streaming]
      ↓
[MessageBus.emit(LLM_TOKEN)] ← (con reasoning separado)
      ↓
[SplitStage.onToken] ← (acumula buffer)
      ↓
[detecta frontera: . ! ? , ; : + coordinantes]
      ↓
[sanitizeForTTS] → [looksLikeReasoning] ← (filtros)
      ↓
[MessageBus.emit(TTS_REQUEST)]
      ↓
[TTSStage] → [WebSocket send] → [TTS server Piper]
      ↓
[Audio chunks PCM] → [WebSocket onmessage] → [playNextChunk]
      ↓
[AudioContext] → [Altavoces]
```

La sanitizacion se aplica en DOS puntos: el SPLIT stage (token por token y al final de cada respuesta) **y** `sendTTS()` — el path manual del boton "Repetir" / botones de audio de cada mensaje. Antes de la build `d`, el path manual saltaba el sanitizer y el TTS leia `#`, `*`, `_` crudos.

### Sentence Splitter (por clausulas, no frases)

Corta en fronteras que el TTS puede manejar independientemente:
- Signos de puntuacion: `.`, `!`, `?`, `,`, `;`, `:`, `¿`, `¡`
- Coordinantes: `y`, `pero`, `aunque`, `sin embargo`, `no obstante`, `por lo tanto`
- Maximo 80 chars por chunk
- Los saltos de parrafo se convierten en una sola pausa (sin `..` ni `!.` en el TTS)
- Minimo 15 chars (no envia palabras sueltas)

### Sanitizer (limpia markdown antes de TTS)

| Patron input | Output al TTS |
|--------------|---------------|
| `**bold**` | `bold` |
| `*italic*` | `italic` |
| `__bold__` / `_italic_` | `bold` / `italic` |
| `# Header` (en cualquier posicion, no solo inicio de linea) | `Header` |
| `### 1. Nombres` | `Nombres` (quita numeracion de enumeracion) |
| `` `code` `` | `code` (contenido protegido: conserva `#`, `*`, `_`) |
| ` ```code blocks``` ` | (eliminado completo) |
| `[text](url)` | `text` |
| `> quote` | `quote` |
| `- item`, `* item`, `1. item` | `item` |
| `C#`, `F#` | conservados (tecnicos, pegados a letra) |
| `2 * 3` | `2 por 3` (multiplicacion preservada) |
| `(parentesis sobrantes)` | eliminados |
| Emojis (👋, 😊, etc) | (eliminados, incl. variation selectors) |
| `Thinking Process:`, `Let me think...` | (eliminados) |

**Excepciones tecnicas (build d):** el contenido entre backticks se extrae, limpia el resto, y se restaura tal cual — asi `pip install # paquete`, `snake_case` o `*ptr*` se leen completos en contextos de codigo, mientras el texto conversacional queda limpio.

### ReasoningDetector (bloquea razonamiento infiltrado en content)

10 patrones regex estrictos:
- `1. **Analyze**`, `2. **Determine**` (markdown headers con numeracion)
- `Thinking Process:`
- `Let me think`
- `I need to analyze/determine/consider`
- `Okay, so...`, `Sure, let me...`
- `Step 1`, `Step 2`
- `**Analyze Request:**`, `**Determine Content:**`
- `User: "..."` (citas del prompt)
- `Intent:`, `Drafting:`

### Razonamiento desactivado por defecto (build `20261110`)

El LLMStage envia `reasoning_effort: "none"` salvo que se marque la casilla **Razonamiento del LLM** en la card Servicios. En el log de produccion el hueco `fetch OK` → primer chunk era de 6-59 s porque gemma razonaba antes de responder. Ademas:
- el razonamiento de turnos anteriores ya no se reinyecta en el historial (`toMessages()` solo manda `content`);
- con auto-TTS activo, el system prompt lleva el estilo de voz (1-3 frases cortas, sin listas/markdown/emojis), igual que `/ws/voice`;
- `max_tokens` por defecto baja a 400.

### Separacion reasoning_content vs content

Los thinking models (`google/gemma-4-e2b`, `qwen3.5-0.8b-mtp`, etc.) emiten 2 campos:
- `delta.content` → texto visible (va al TTS + chat)
- `delta.reasoning_content` → razonamiento interno (solo al chat, colapsable)

El harness separa ambos y muestra el razonamiento en un bloque "🧠 Razonamiento interno" colapsable, en gris italica.

### Validacion de voz (TTS bloqueado sin voz)

- `AppState.currentVoiceSet` se activa **solo** cuando el usuario hace click explicito en un voice chip
- `isVoiceReady()` valida antes de cada `flushChunk` o `sendChat` con auto-TTS
- Si auto-TTS esta ON pero no hay voz: confirm() pregunta si enviar sin TTS
- Label "Auto-TTS" se atenua a 40% opacity con tooltip explicativo

### Barge-in Gold (interrupcion end-to-end del run)

Desde la build `b`, el barge-in viaja por el run completo, no solo TTS:

```
stopGeneration() → [Gold-grade barge-in]
 1. anti-loop: rate limiter (max 4 barge-ins/10s, lock 5s)
 2. stop audio local + vaciar audioQueue/bufferedChunks
 3. emit INTERRUPT en el bus → aborta LLMStage (AbortController)
 4. ws.send {type:'interrupt', run_id, reason:'barge-in'}
 5. conversationLog.truncate(playedAudioMs, wordOffsetsMs)
    + mark_interrupted('assistant','barge-in')
 6. activeRunId = null → los frames binarios tardios se descartan
 7. FSM → INTERRUPTED → re-arm de AudioCapture (RECORDING)
```

Ademas:
- El WS cliente hace **fence por run_id** (P0.5): mensajes JSON con `run_id` distinto al activo se descartan; frames binarios con `activeRunId === null` se descartan (build `c`).
- `activeRunId` ya NO se limpia en el `finally` del LLMStage: se limpia solo en `catch AbortError` con guarda — asi el audio TTS del ultimo chunk llega en vez de cortarse en seco (fix de respuestas mudas, build `c`).

### Captura de voz (STT)

El harness envia `vad_filter=false` al STT (el corte ya lo hace el VAD del browser por RMS). Ruido/estatica y errores de reconocimiento con utterances largas son pendientes conocidos — ver [Roadmap](#roadmap).

---

## UI completa y controles

### Card "Servicios"

| Control | Default | Funcion |
|---------|---------|---------|
| URL STT | http://localhost:8100 | Endpoint del STT microservice |
| URL LLM | http://localhost:8990/llm/v1 | Proxy OpenAI-compat (websocket_server) |
| Modelo LLM | google/gemma-4-e2b | Catalogo con datalist (ver abajo) |
| URL WS TTS | ws://localhost:8990/ws/tts | Endpoint del TTS WebSocket |
| Max tokens | 800 | **Minimo 500 para thinking models** |
| System Prompt | "Eres un asistente amigable..." | Instrucciones para el LLM |

#### Catalogo de modelos LLM (input con sugerencias)

El campo "Modelo LLM" es un `<input list="llmModelCatalog">`: se puede escribir cualquier modelo custom, y el desplegable sugiere:

| Valor | Etiqueta |
|-------|----------|
| `qwen3.5-0.8b-mtp` | Qwen3.5 0.8B (MTP) |
| `qwen2.5:0.5b-instruct-q8_0` | Qwen2.5 0.5B (Q8_0) |
| `qwen2.5:1.5b-instruct-q8_0` | Qwen2.5 1.5B (Q8_0) |
| `google/gemma-3n-e4b` | Gemma 3N E4B |
| `google/gemma-4-e2b` | Gemma 4 E2B |
| `google/gemma-2-2b` | Gemma 2 2B |

El cliente modular Vite (`client/src/config/panel.js`) usa el mismo catalogo como `<select>` y agrega `(custom)` si el valor guardado en localStorage no esta en la lista.

### Card "Captura de voz"

| Control | Default | Funcion |
|---------|---------|---------|
| Modelo STT | parakeet | parakeet (recomendado) / whisper tiny/base/small |
| Umbral voz (RMS) | 0.012 | Sensibilidad del VAD |
| Silencio para cortar | 700ms | Tiempo antes de transcribir |
| Idioma | Espanol | es/en/auto |
| Filtros DSP | Medio | off/low/medium/high |
| Barge-in auto | ON | Si OFF, no interrumpe con voz. Umbral `max(0.012, umbral×1.6)` durante 2 bloques seguidos; si no salta, el log muestra `[barge-in saltado] … max=… req=…` |

### Card "TTS"

| Control | Default | Funcion |
|---------|---------|---------|
| Voz | (seleccion manual) | Click en chip para cambiar |
| Velocidad | 1.0x | Multiplicador |
| Auto-TTS | ON | Si OFF, solo texto |
| Audio streaming | ON | Si OFF, espera a frase completa |

### Card "Estado"

- **State chip** con color por estado
- 3 puntos de servicio (STT/LLM/TTS): verde=online, rojo=offline
- Circuit Breaker status + countdown bar
- Stats: TTFB, chunks, tokens

### Card "Chat"

- Mensajes del usuario (derecha, azul) y assistant (izquierda, blanco)
- Bloque colapsable "🧠 Razonamiento interno" en cada respuesta
- Boton "🔊 Reproducir" en cada respuesta del assistant
- Input + Enter o boton Enviar
- Boton ⏸ Stop (barge-in manual)

### Card "Log"

- Ultimos 200 entries con timestamp
- Formato: `[HH:MM:SS] [trace:xxxxxxxx] [stage] mensaje`
- Niveles: info (azul), success (verde), warn (amarillo), error (rojo)

### Card "Trazas (observabilidad)"

- 5 metricas: total, exito, tasa%, avg ms, p95 ms
- Ultimas 15 trazas con timeline de eventos
- Status final (success/failed/running)

### Card "Eventos del sistema"

- Ultimos 40 eventos del browser
- Filtros por categoria: DOM, WebSocket, Window, Mic
- Timestamp ms-precision
- Payload resumido

---

## Performance esperado

> **Actualizado (nov-2026)**: con Parakeet, `reasoning_effort: none` y Piper en streaming, las
> latencias vigentes estan en [ARCHITECTURE.md §9](ARCHITECTURE.md#9-latencias-y-cuellos-de-botella)
> y [REALTIME.md](REALTIME.md#latencias-medidas-python-teststest_voice_ws_e2epy). Las tablas
> siguientes son historicas (build d, whisper tiny, thinking activo).

En Intel i7-11700, 32GB RAM, sin GPU:

| Operacion | Latencia | Throughput |
|-----------|----------|-----------|
| STT (tiny) | 0.5s para 2s audio | RTF 0.24 (4x realtime) |
| STT (base) | 1.0s para 2s audio | RTF 0.50 (2x realtime) |
| LLM gemma-4-e2b | TTFT 3-4s | ~30 tokens/s |
| LLM qwen3.5-9b | TTFT 2-3s | ~40 tokens/s |
| TTS Piper | first-byte 2-3.5s | 500x realtime |
| Pipeline completo | 5-8s input -> output | - |

Tiempos reales medidos en smoke tests:

| Test | STT | LLM TTFT | LLM total | TTS first | TTS total |
|------|-----|----------|-----------|-----------|-----------|
| Saludo (2.4s) | 2.6s | 3.5s | 26s | 2.4s | 2.4s |
| Pregunta (2.2s) | 2.5s | 3.4s | 34s | 3.0s | 3.1s |
| Largo (4.8s) | 2.5s | 3.5s | 34s | 2.9s | 2.9s |

---

## Troubleshooting

### "STT: OFFLINE"

```bash
curl http://localhost:8100/health
# Debe devolver {"status":"ok","model_loaded":true,...}
```

Si no responde, arrancarlo: `python server/stt/stt_server.py --port 8100`.

### "LLM: OFFLINE"

- Verificar LM Studio abierto y con un modelo cargado
- Probar en el browser: http://localhost:12344/v1/models
- Si usas Ollama, cambiar URL a http://localhost:11434/v1

### "TTS: OFFLINE"

```bash
curl http://localhost:8990/health
```

Si no responde, arrancarlo: `python server/tts/websocket_server.py`.

### El mic no se activa

- El browser pide permiso la primera vez. Aceptalo.
- Si lo rechazaste: configuracion del browser > Privacidad > Permisos > microfono
- Si abriste el HTML desde `file://`, algunos browsers lo bloquean. Usa el servidor local: `python serve_nocache.py 5500` y abre `http://localhost:5500/client/client_example.html`

### El bot no responde (queda pensando para siempre)

- Mira el indicador **Circuit LLM**: si esta en OPEN, espera la cuenta atras o haz clic para reiniciarlo.
- Si activaste **Razonamiento del LLM**, el modelo puede gastar todos los `max_tokens` pensando: desactivalo o sube el slider (800+).
- En LM Studio, baja *parallel* a 1-2 y deja el modelo cargado (sin TTL).

### La transcripcion esta vacia o tiene palabras raras

- Usa `parakeet` (por defecto): WER 2.5 % en espanol frente al 26 % de whisper `tiny`.
- Verificar idioma correcto (Espanol vs Ingles).
- Si el audio tiene mucho ruido, subir threshold de VAD a 0.03-0.05.

### El microfono se apaga solo

Desde la build `20261110` no deberia: solo lo apagan el clic de raton o el comando de voz. Si pasa, busca en el log `Microfono detenido (motivo)` y `[mic watchdog]`; el motivo indica quien lo paro.

### El bot se interrumpe solo (barge-in falso positivo)

- Bajar sensibilidad del barge-in: subir threshold de VAD a 0.04+
- O desactivar barge-in auto y usar solo el boton ⏸

### Primer TTS no suena (pero los siguientes si)

Chrome autoplay policy: ya esta fix en v10. Si pasa, abrir `chrome://settings/content/sound` y verificar que "Sites can play sound" esta ON. Si abres desde una IP externa (no localhost), necesitas HTTPS.

### "Thinking Process" o "1. **Analyze**" se escucha en TTS

Si razonamiento se escapa al TTS, verifica que el sanitizer este en build d+. Si pasa, es un modelo que emite patrones nuevos — avísame y los agrego a `looksLikeReasoning()`.

---

## Limitaciones conocidas

### Razonamiento de los thinking models

Con LM Studio actual, `reasoning_effort: "none"` desactiva el razonamiento de `gemma-4-e2b` (medido en `/ws/voice`: primer token ~190 ms en caliente). Si un modelo lo ignora, el filtro client-side (`ReasoningDetector`) sigue evitando que el razonamiento llegue al TTS.

### Whisper en espanol

Whisper `tiny` confunde 1 de cada 4 palabras; por eso el defecto es Parakeet TDT 0.6B v3 (`onnx-asr`). Whisper queda como alternativa (`STT_MODEL=base|small`).

### El puerto 8000 esta ocupado

Hay un proceso Python tuyo previo usando :8000. El STT usa :8100 para evitar conflicto. Si quieres liberar :8000, mata ese proceso manualmente y rearranca el STT alli.

### `getUserMedia` requiere localhost o HTTPS

Abre el HTML con doble click (`file://`) o sirviéndolo desde `localhost`. IPs externas requieren HTTPS.

### El harness clasico transcribe por turno

El harness espera al silencio (700ms) y transcribe el chunk completo por HTTP. El camino realtime (`/ws/voice`, [REALTIME.md](REALTIME.md)) ya usa Silero-VAD y STT especulativo, con ~0.5 s de fin de turno a primer audio.

---

## Roadmap

### Completado (build c/d, 09-ago-2026)
- [x] Barge-in Gold: cancelacion end-to-end del run (LLM abort + TTS interrupt por run_id + truncate + mark_interrupted)
- [x] Fence de eventos stale por run_id + descarte de frames binarios huerfanos
- [x] Fix de respuestas mudas: activeRunId limpiado solo en AbortError, no en finally
- [x] Sanitizer robusto con excepciones tecnicas + `sendTTS()` sanitizado (boton Repetir)
- [x] Catalogo de modelos LLM (datalist en harness, select en cliente Vite)
- [x] Marcador ⚡ de interrupcion scoped al branch INTERRUPTED (no global)

### Completado (nov-2026)
- [x] Realtime `/ws/voice` + SDK `client/sdk/` (Silero, STT especulativo, barge-in con truncado a lo oido)
- [x] STT Parakeet por defecto (clasico y realtime), LLM por IPv4, warmup del LLM
- [x] Mic siempre encendido (watchdog, reintentos sin limite, comando de voz "apagar")
- [x] Circuit breaker: fallos seguidos, HALF_OPEN automatico, aborts no cuentan, reinicio con clic
- [x] LLM sin razonamiento por defecto, sin reasoning en el historial, estilo de voz, max_tokens 400
- [x] Barge-in mas sensible con confirmacion de 2 bloques, descarte de capturas de ruido, fix `..` en TTS

- [x] TTS por HTTP (`POST /tts`, `/v1/audio/speech`), streaming en vivo, cliente CLI `/ws/tts` y API key (`API_KEYS`)

### Pendiente
- [ ] **LLM remoto con clave propia** (OpenAI, OpenRouter, Groq, Ollama/vLLM en otra maquina). Plan:
  1. `LLM_API_KEY` en `server/shared/config.py` y `.env.template` (vacio = sin cabecera, LM Studio local).
  2. Proxy `/llm/*` (`server/tts/websocket_server.py`): quitar del reenvio `Authorization`, `X-API-Key` y `Cookie` del cliente (hoy la API key del TTS se reenviaria al proveedor) y poner `Authorization: Bearer $LLM_API_KEY` si existe.
  3. `OpenAICompatLLM` (`server/realtime/engines.py`, usado por `/ws/voice`): aceptar `api_key` y mandarlo en la misma cabecera.
  4. Quitar el `?api_key=` del query string reenviado al upstream.
  5. Tests: el proxy no reenvia la clave del cliente, si inyecta `LLM_API_KEY`; el engine manda la cabecera. Docs en README/MANUAL.
- [ ] Crash al cambiar configuraciones del panel (en investigacion, requiere logs F12 + stdout)
- [ ] Usar `/ws/voice` como modo opcional dentro del harness clasico
- [ ] Boton "Exportar trazas a JSON" para debug
- [ ] WebRTC para audio de baja latencia
- [ ] Persistencia de conversaciones (SQLite/JSON)

---

## Archivos del proyecto (actualizado 10-nov-2026)

Estructura actual verificada contra el codebase:

```
.
├── README.md                       # Entry point del proyecto
├── LICENSE
├── .env, .env.template, .gitignore
│
├── serve_nocache.py                # Servidor estatico :5500 sin cache (evita 304)
├── scripts_check_monolith.py       # Chequeo de sintaxis del monolito (JS embedded)
├── requirements-tts.txt, requirements-stt.txt   # Dependencias de los dos servers
│
├── client/                         # Cliente browser
│   ├── client_example.html         # Harness monolito (~4400 lineas, HTML+JS vanilla) - EL QUE CORRE
│   ├── sdk/                        # SDK realtime (RealtimeVoiceClient, worklets, demo.html)
│   ├── index.html                  # Entry del cliente Vite modular
│   ├── src/                        # Cliente modular (escrito, no servido)
│   │   ├── config/ (panel.js, store.js)
│   │   ├── realtime/ (localWS.js, turnController.js, runContext.js, ...)
│   │   ├── state/, session/, events/, sync/, chat/, inspector/, observability/
│   └── tests/                      # Tests del cliente modular (vitest)
│
├── server/                         # Backend Python
│   ├── tts/                        # TTS WebSocket + proxy LLM embebido
│   │   └── websocket_server.py     #   FastAPI :8990 (WS /ws/tts + POST /llm/{path})
│   ├── stt/                        # STT microservice
│   │   └── stt_server.py           #   FastAPI + Parakeet/faster-whisper :8100
│   ├── realtime/                   # /ws/voice: session, vad, engines, chunker, protocol, ws
│   ├── shared/                     # Codigo compartido entre servers
│   │   ├── observability/          #   tracer, metrics, middleware, otlp, exporters
│   │   ├── events/                 #   bus, envelope, store (sqlite/memory/jsonl/postgres/sqlserver)
│   │   ├── conversation/           #   log, turn, turn_controller, cancel_modes, machine
│   │   ├── session/, state/        #   Contexto de sesion + FSMs
│   │   ├── backends/               #   base, kokoro, piper, xtts
│   │   └── config.py, llm.py, result.py, speech.py, audio.py, ...
│   └── legacy_kokoro/              # Codigo legacy Kokoro (no se usa)
│
├── tests/                          # Tests del sistema
│   ├── smoke_e2e.py, smoke_test_ws.py, test_harness_e2e.py
│   ├── gold/ (fenced bus), observability/, events/, state/, result/, conversations/
│   └── ...
│
├── docs/                           # Documentacion
│   ├── MANUAL.md                   #   Este documento
│   ├── ARCHITECTURE.md             #   Arquitectura + diagramas de flujo y estados
│   ├── REALTIME.md                 #   Protocolo /ws/voice, SDK y latencias
│   ├── OBSERVABILITY.md            #   Trazas/metricas/eventos end-to-end
│   ├── CLEANUP_HISTORY.md          #   Log de limpiezas historicas
│   └── architecture/               #   e2e-voice-flow.html + INFORME_GOLD_*.md
│
├── data/                           # Modelos
│   ├── piper_voices/               #   Modelos Piper (.onnx + .json), se descargan solos (ignorado)
│   └── models/                     #   Modelos legacy Kokoro
│
├── output/                         # Outputs generados (gitignored, .gitkeep)
├── recordings/                     # Audio de usuario (gitignored, .gitkeep)
└── logs/                           # Logs de servicios (gitignored, .gitkeep)
```

**Estado actual**: el monolito `client_example.html` es el cliente activo. El cliente Vite (`client/src/`) esta escrito y testeado pero no se sirve. BUILD_ID actual: `kokoro-bargein-d-20261110-1`.

---

## Versionado

| Version | Cambios |
|---------|---------|
| v1 | Kokoro original (Kokoro-82M) |
| v2 | Multi-backend (Kokoro/Piper/XTTS via TTSFactory) |
| v3 | WebSocket server con multi-backend |
| v4 | Cliente web con chat LLM + TTS |
| v5 | Boton Conectar TTS, probe servicios, auto-reconexion |
| v6 | State machine, circuit breaker, VAD browser, barge-in, sentence splitter |
| v7 | Separacion reasoning/content + sanitizer TTS |
| v8 | Mic resiliente (auto-reconexion, distingue errores, UI con estados) |
| v9 | Filtros DSP browser-side (highpass, lowpass, gate, preemph, spectral) |
| v10 | Fix TTS live (pre-warm AudioContext para Chrome autoplay policy) |
| v11 | Voz obligatoria + trazabilidad end-to-end con trace_id |
| v12 | Message Bus (pub/sub) + Pipeline de stages + State machine per-message |
| v13 | EventManager (DOM, WS, Network) + UI de eventos con filtros |
| build b (`kokoro-bargein-b-...`) | Barge-in Gold: run-aware interrupt TTS + LLMStage.abort por bus + re-arm del mic |
| build c (`kokoro-bargein-c-20260809-3`) | Fix respuestas mudas (activeRunId solo en AbortError), purge de huerfanos null-runId, fence binario, marcador ⚡ scoped |
| build d (`kokoro-bargein-d-20260809-4`) | Sanitizer robusto con excepciones tecnicas + sendTTS sanitizado + catalogo de modelos LLM |
| `kokoro-bargein-d-20261110-1` | Mic siempre encendido + comando "apagar", circuit breaker corregido, LLM sin razonamiento y respuestas cortas, barge-in mas sensible, descarte de ruido, fix `..` |

---

## Resumen ejecutivo

El harness es una **arquitectura de mensajeria completa** con:

- **3 microservicios desacoplados** (STT, LLM, TTS) comunicandose por HTTP/WS
- **1 cliente browser** (sin servidor) que implementa:
  - State machine global + state machine per-message
  - Pub/Sub Message Bus con pipeline de stages
  - Circuit Breaker para LLM
  - Tracer con trace_id end-to-end
  - EventManager que captura DOM/WS/Network
  - AudioDenoiser con 5 capas DSP
  - SentenceSplitter + Sanitizer para TTS limpio
  - Resiliencia: auto-reconexion mic, WS, services
  - UI con cards de chat, trazas, eventos, log, estado

**Total**: ~4400 lineas de HTML/JS en el monolito, ~500 lineas de Python (STT server), FastAPI + Piper (TTS), 3 servicios + proxy LLM embebido, 0 acoplamiento entre capas.

Desarrollado incrementalmente durante esta sesion con validacion empirica en cada feature (smoke tests contra el server real, sintaxis validada con node, denoiser testado contra 4 escenarios sinteticos).
