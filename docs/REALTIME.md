# Realtime Voice (`/ws/voice`)

Voz en vivo 100 % local, estilo OpenAI Realtime: hablas de forma continua, puedes
interrumpir a la IA a mitad de frase (barge-in), si sigues hablando tras una pausa
se reprocesa todo junto, y el contexto guarda solo lo que realmente escuchaste.

```
mic PCM16 16 kHz ─► VAD Silero (32 ms) ─► pausa 128 ms: STT especulativo ─► fin de turno (350 ms) ─► Parakeet TDT 0.6B (ONNX int8)
        │ voz nueva mientras habla la IA = BARGE-IN (cancela LLM + TTS, trunca contexto)
        ▼
LM Studio (SSE, reasoning_effort=none) ─► corte por cláusula ─► Piper streaming ─► PCM16 binario
```

Todo va por **un único WebSocket** full-duplex en el servidor TTS (`ws://host:8990/ws/voice`).

## Piezas

| Pieza | Ruta | Para qué |
|-------|------|----------|
| Núcleo servidor | `server/realtime/` | `RealtimeSession` (sin FastAPI), reutilizable en cualquier servidor ASGI |
| Adaptador ASGI | `server/realtime/ws.py` | `serve_websocket(ws, stt=, llm=, tts=, config=)` |
| Motores | `server/realtime/engines.py` | `ParakeetSTT` (default), `WhisperSTT`, `OpenAICompatLLM`, `BackendTTS` (intercambiables) |
| SDK navegador | `client/sdk/` | `RealtimeVoiceClient` + AudioWorklets (mic 16 kHz, reproductor con flush) |
| Demo | `client/sdk/demo.html` | `http://localhost:5500/client/sdk/demo.html` |

### Uso del SDK

```js
import { RealtimeVoiceClient } from './sdk/index.js';

const rt = new RealtimeVoiceClient({
  url: 'ws://localhost:8990/ws/voice',
  session: { instructions: 'Eres breve.', language: 'es', speed: 1.1 },
});
rt.addEventListener('transcript', (e) => console.log('tú:', e.detail.text));
rt.addEventListener('text',       (e) => console.log('ia:', e.detail.delta));
rt.addEventListener('truncated',  (e) => console.log('escuchado:', e.detail.heard_text));
rt.addEventListener('metrics',    (e) => console.table(e.detail));
await rt.connect();
await rt.startMic();
// rt.sendText('hola'), rt.cancel(), rt.updateSession({...}), rt.clearConversation()
```

El audio se abstrae con un adaptador (`WebAudioIO` por defecto); otro cliente
(Electron, React Native, tests) puede inyectar el suyo con `{ audio }`.

### Uso del núcleo en otro servidor

```python
from server.realtime import RealtimeSession, SessionConfig
session = RealtimeSession(send_json, send_bytes, stt=mi_stt, llm=mi_llm, tts=mi_tts,
                          config=SessionConfig(instructions="..."))
await session.start()
await session.feed_audio(pcm16_bytes)   # por cada frame del micro
await session.handle_event(evento_json) # por cada mensaje de texto
```

Interfaces de los motores: `stt.transcribe(audio_f32_16k, language) -> str`,
`llm.stream(messages, **opts) -> AsyncIterator[str]`,
`tts.sample_rate` + `tts.synthesize(text, speed) -> Iterator[np.ndarray]`.

## Protocolo (v1)

**Binario**: cliente → servidor, PCM16 LE mono a 16 kHz (20 ms recomendado).
Servidor → cliente, cabecera de 8 bytes `<u32 response_id><u32 seq>` seguida de PCM16 LE
a `output_sample_rate`, que se anuncia en `session.created`.

| Cliente → servidor | Campos |
|--------------------|--------|
| `session.update` | `session: {instructions, model, temperature, max_tokens, language, speed, barge_in, min_speech_ms, vad: {threshold, silence_ms, prefix_ms, ...}}` |
| `input_audio.commit` / `input_audio.clear` | fuerza fin de turno / descarta el audio en curso |
| `conversation.item.create` | `text`: turno de usuario escrito |
| `response.cancel` | parar la respuesta activa |
| `playback.position` | `response_id, played_ms` (~cada 100 ms) |
| `response.truncate` | `response_id, played_ms` tras cortar el audio por barge-in |
| `conversation.clear`, `ping` | |

| Servidor → cliente | Campos |
|--------------------|--------|
| `session.created` / `session.updated` | `session, audio{input_sample_rate, output_sample_rate}` |
| `input_audio.speech_started` | `audio_ms, response_id?` (si hay respuesta: barge-in) |
| `input_audio.speech_stopped` | `audio_ms` |
| `transcript.final` | `response_id, text` |
| `response.created` / `response.text.delta` / `response.segment` | `response_id`, `delta` / `index, text, start_ms` |
| `response.done` | `response_id, text, audio_ms` |
| `response.cancelled` | `response_id, reason (barge-in\|user_continued\|cancelled\|error), audio_ms, interrupt_ms` |
| `conversation.truncated` | `response_id, heard_text, played_ms` |
| `metrics.turn` | `stt_ms, llm_first_token_ms, first_clause_ms, tts_first_chunk_ms, first_audio_ms, total_ms, gateway{p50_ms,p99_ms}` |
| `error`, `pong` | |

### Semántica de interrupción

- **Barge-in** (ya sonó audio): el SDK silencia en local en cuanto recibe `speech_started`.
  El servidor cancela el LLM y el TTS, emite `response.cancelled`, y el cliente responde
  con `response.truncate{played_ms}`. En el contexto solo queda lo escuchado (`heard_text…`),
  marcado como `interrupted`.
- **Reproceso** (aún no sonó audio): si hablas tras una pausa corta, la respuesta en curso se
  descarta (`reason: user_continued`) y el audio anterior se une al nuevo y se transcribe de una vez.
- El historial que recibe el LLM fusiona turnos consecutivos del mismo rol, porque Gemma
  exige alternancia user/assistant.

## STT: por qué Parakeet

Benchmark con 60 frases en español (~3 s cada una), limpias y con ruido a 12 dB SNR, en CPU (i7-11700):

| Motor | WER limpio | WER ruido | Latencia p50 |
|-------|-----------|-----------|--------------|
| whisper tiny, beam 1 (antes) | 26.3 % | 41.6 % | 535 ms |
| whisper base, beam 5 | 9.1 % | 18.9 % | 1623 ms |
| whisper small, beam 5 | 5.3 % | 8.6 % | 4208 ms |
| **Parakeet TDT 0.6B v3 int8** | **2.5 %** | **7.8 %** | **261 ms** |

Parakeet (`onnx-asr`) es más preciso que whisper small y unas 16 veces más rápido. Si `onnx-asr` no
está instalado, se usa whisper base con beam 5.

**STT especulativo**: con 128 ms de silencio, el VAD emite una "pausa" y el STT empieza ya. Si a los
350 ms se confirma el fin de turno, se reutiliza ese resultado, y si sigues hablando se descarta.
Así el STT queda casi oculto tras el silencio de fin de turno (`metrics.turn.stt_speculative`).

## Configuración (`.env`)

| Variable | Default | |
|----------|---------|-|
| `REALTIME_STT_ENGINE` | `parakeet` | `parakeet` o `whisper` |
| `REALTIME_STT_QUANT` | `int8` | cuantización de Parakeet (`fp32` = sin cuantizar) |
| `REALTIME_STT_THREADS` | `0` | hilos de CPU para STT (0 = automático) |
| `REALTIME_STT_MODEL` | `base` | solo whisper (`small` es más preciso pero ~4 s) |
| `REALTIME_STT_DEVICE` / `REALTIME_STT_COMPUTE` | `cpu` / `int8` | solo whisper |
| `REALTIME_STT_BEAM` | `5` | solo whisper |
| `REALTIME_LLM_REASONING` | `none` | `reasoning_effort` enviado a LM Studio; vacío = no enviar |
| `REALTIME_PRELOAD` | `1` | carga y calienta STT/Piper/VAD y pide 1 token al LLM al arrancar |
| `STT_MODEL` | `parakeet` | modelo por defecto del servidor STT clásico (:8100, modo local-chain) |
| `SPEED` / `LLM_TEMPERATURE` / `DEFAULT_SYSTEM_PROMPT` | `1.2` / `0.7` / — | también se aplican a `/ws/voice`, y el prompt lleva reglas de voz (frases cortas, sin emojis ni markdown) |
| `LM_STUDIO_URL` | `http://127.0.0.1:12344/v1` | usa IPv4 (ver abajo) |

> **Windows y `localhost`**: `localhost` resuelve primero a `::1`, pero LM Studio solo escucha en IPv4,
> así que cada conexión nueva tardaba ~2.3 s. Además, el barge-in cierra la conexión, con lo que el
> siguiente turno volvía a pagar ese retraso. El servidor reescribe `//localhost` a `//127.0.0.1`
> automáticamente.

## Latencias medidas (`python tests/test_voice_ws_e2e.py`)

CPU local, Piper, Parakeet int8, `gemma-4-e2b` en LM Studio (CPU):

| Etapa | Antes | Ahora |
|-------|-------|-------|
| Overhead del gateway por frame (p50 / p99) | 0.4–0.7 / 1.9–3.0 ms | 0.4–0.9 / 1.2–1.3 ms |
| Barge-in en servidor (voz detectada → cancelado) | ~9 ms | ~5 ms |
| STT tras el fin de turno | 450–730 ms | ~0.5 ms (especulativo) |
| LLM primer token | 0.8–2.3 s | ~190 ms (en caliente) |
| Primera cláusula lista | — | ~425 ms |
| TTS primer chunk | 75–280 ms | 50–70 ms |
| Fin de turno → primer audio (servidor) | ~3.6–5 s | **~480 ms** |

Al arrancar, el servidor pide 1 token a LM Studio para que cargue el modelo (JIT); así el primer
turno queda en ~0.9 s en vez de ~2.1 s. LM Studio descarga el modelo tras 1 h sin uso (TTL), así que
conviene desactivar ese TTL o cargarlo fijo. Lo que queda depende sobre todo de la velocidad del LLM
en CPU (~12 tok/s): con un modelo más pequeño o con offload a GPU, la respuesta empieza a sonar antes.
