# Servidor TTS - KOKORO

Asistente de voz local en tiempo real con arquitectura de microservicios:

- **STT** (Speech-to-Text) con Parakeet TDT 0.6B v3 (por defecto) o faster-whisper
- **LLM** con LM Studio u Ollama (externo, vía proxy `/llm/*` del TTS), sin razonamiento por defecto
- **TTS** (Text-to-Speech) multi-backend: Piper (por defecto), Kokoro, XTTS
- **Cliente** en el navegador con micro siempre encendido (se apaga a mano o diciendo "apagar"), barge-in (interrupción), circuit breaker del LLM, trazas y métricas
- **Realtime** `/ws/voice`: todo el pipeline en el servidor por un solo WebSocket, ~0.5 s de fin de turno a primer audio

Arquitectura y diagramas de flujo: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Estructura del proyecto

```
.
├── start.cmd / stop.cmd            # Lanzador Windows (STT + TTS + HTTP)
├── _run_{stt,tts,http}.cmd         # Un servicio cada uno (los usa start.cmd)
├── program.py                      # Lanzador único para depurar desde el IDE (F5)
├── serve_nocache.py                # Servidor estático del cliente sin caché
├── .env.template                   # Plantilla de configuración (.env no se commitea)
│
├── client/
│   ├── client_example.html         # Cliente principal (HTML+JS en un solo archivo, sin build)
│   ├── sdk/                        # SDK realtime (RealtimeVoiceClient + AudioWorklets + demo.html)
│   ├── index.html + src/           # Cliente modular (Vite + ES modules), en migración
│   └── tests/                      # Tests vitest del cliente modular
│
├── server/
│   ├── stt/stt_server.py           # Microservicio STT (FastAPI + Parakeet / faster-whisper)
│   ├── tts/websocket_server.py     # Servidor TTS (WebSocket /ws/tts y /ws/voice, proxy /llm, voces)
│   ├── realtime/                   # Núcleo de /ws/voice (session, vad, engines, chunker, protocol)
│   └── shared/                     # Código compartido
│       ├── config.py               # Settings (pydantic-settings, lee .env)
│       ├── net.py                  # BIND_HOST / CORS por defecto (solo local)
│       ├── backends/               # TTS multi-backend (Strategy): piper, kokoro, xtts
│       ├── conversation/           # ConversationLog, TurnController, modos de cancelación
│       ├── events/                 # Event bus + event store (memory, jsonl, sqlite, postgres, sqlserver)
│       ├── observability/          # Tracer, métricas, exportador OTLP, middleware
│       ├── session/ state/         # Contexto de sesión y máquinas de estado
│       ├── result.py               # Tipo Result (éxito/error sin excepciones)
│       ├── llm.py, speech.py, ...  # Cliente LLM, VAD/STT, audio, chunker, voces
│
├── tests/                          # pytest (unitarios + e2e que requieren servicios)
├── docs/                           # MANUAL, OBSERVABILITY, arquitectura
├── shared/events.json              # Catálogo de eventos compartido cliente/servidor
├── assets/                         # Diagramas e imágenes del proyecto original
└── data/ output/ recordings/ logs/ # Datos y artefactos en runtime (ignorados)
```

## Quickstart

### 1. Instalar dependencias

```bash
pip install -r server/tts/requirements-tts.txt
pip install -r server/stt/requirements-stt.txt
```

### 2. Configurar

```bash
copy .env.template .env
```

### 3. Arrancar

```bash
start.cmd
```

o, desde el IDE:

```bash
python program.py
```

LM Studio se arranca aparte (app de escritorio): carga un modelo (ej. `google/gemma-4-e2b`) e inicia el servidor local en el puerto **12344**.

### 4. Abrir el cliente

http://localhost:5500/client/client_example.html

> `getUserMedia` (micrófono) requiere HTTPS, `localhost` o `file://`.

## Red y seguridad

Por defecto **todo escucha solo en `127.0.0.1`** y CORS acepta únicamente orígenes `localhost` y `file://`. El proxy `/llm/*` reenvía peticiones a tu LLM, así que no lo expongas sin necesidad. Para usar el stack desde otro equipo de la LAN:

```bash
set BIND_HOST=0.0.0.0
set CORS_ORIGINS=http://192.168.1.50:5500
start.cmd
```

El servidor estático (`serve_nocache.py`) nunca sirve archivos ocultos (`.env`, `.git/…`).

### Clientes fuera de tu red: API key

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


## Servicios

| Servicio | Puerto | Stack | Endpoints útiles |
|----------|--------|-------|------------------|
| STT | 8100 | FastAPI + Parakeet / faster-whisper | `/health`, `/transcribe`, `/models`, `/metrics` |
| TTS | 8990 | FastAPI + WebSocket | `POST /tts`, `POST /v1/audio/speech`, `/ws/tts`, `/ws/voice` (realtime), `/llm/*`, `/voices`, `/health`, `/metrics` |
| LLM | 12344 | LM Studio (OpenAI-compat) | externo |
| Cliente | 5500 | `serve_nocache.py` | `/client/client_example.html` |

### Consumir el TTS por HTTP (curl)

Para pedir audio con una sola petición (scripts, otros servicios, curl) sin abrir un WebSocket:

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

Por WebSocket (`/ws/tts`, streaming; curl no puede mandar frames WS) hay un cliente de línea de comandos que reproduce el audio en vivo mientras llega (sin guardar nada; `-o` para guardar también el WAV, `--no-play` para solo guardar) y admite `wss://`:

```bash
python client/tts_ws_client.py "Hola, esto es una prueba." --voice es_ES/davefx-medium --speed 1.0
```

Reproducir en Windows:

```bash
powershell -c "(New-Object Media.SoundPlayer 'hola.wav').PlaySync()"
```

## Realtime Voice

Voz en vivo local estilo OpenAI Realtime, con barge-in, reproceso y contexto truncado a lo
escuchado, por un único WebSocket `ws://localhost:8990/ws/voice`. El núcleo reutilizable vive en
`server/realtime/` y el SDK de navegador en `client/sdk/`; la demo está en
`http://localhost:5500/client/sdk/demo.html`. Protocolo, SDK y latencias: [docs/REALTIME.md](docs/REALTIME.md).

## Configuración

Ver [.env.template](.env.template). Las más importantes: `TTS_BACKEND`, `LM_STUDIO_URL`, `LLM_MODEL`, `BIND_HOST`, `CORS_ORIGINS`, `API_KEYS`.

## Testing

```bash
# Unitarios Python (no requieren servicios)
python -m pytest tests --ignore=tests/smoke_e2e.py --ignore=tests/smoke_test_ws.py --ignore=tests/test_harness_e2e.py --ignore=tests/test_realtime_e2e.py --ignore=tests/test_stt_e2e.py --ignore=tests/test_voice_ws_e2e.py

# Cliente modular
cd client && npm install && npm test

# E2E (con STT, TTS y LM Studio levantados)
python tests/smoke_test_ws.py
python tests/test_stt_e2e.py
python tests/test_harness_e2e.py
python tests/test_realtime_e2e.py
python tests/test_voice_ws_e2e.py   # /ws/voice: latencia por etapa + barge-in
```

## Documentación

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): arquitectura, diagramas de flujo, secuencia y estados, latencias
- [docs/REALTIME.md](docs/REALTIME.md): `/ws/voice`, protocolo, SDK y benchmark de STT
- [docs/MANUAL.md](docs/MANUAL.md): manual del harness (UI, resiliencia, troubleshooting)
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md): trazas y métricas
- [docs/architecture/](docs/architecture/): flujo de voz e informe Gold
- [docs/CLEANUP_HISTORY.md](docs/CLEANUP_HISTORY.md): historial de limpiezas

## Logs

```bash
Get-Content .\logs\stt_server.log -Wait
Get-Content .\logs\tts_server.log -Wait
```

## Stack

- **Backend**: Python 3.14+, FastAPI, uvicorn
- **Frontend**: HTML5 + JS (monolito) y cliente modular con Vite/vitest
- **TTS**: Piper (VITS/ONNX, castellano nativo); Kokoro y XTTS opcionales
- **STT**: Parakeet TDT 0.6B v3 (ONNX int8, `onnx-asr`) o faster-whisper (CPU)
- **LLM**: LM Studio u Ollama

## Créditos

Basado en [On-Device-Speech-to-Speech-Conversational-AI](https://github.com/asiff00/On-Device-Speech-to-Speech-Conversational-AI)
de Abdullah Al Asif ([@asiff00](https://github.com/asiff00)), MIT. Gracias también a los autores de
[Piper](https://github.com/OHF-Voice/piper1-gpl) y sus voces, [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M),
[Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) (NVIDIA), [onnx-asr](https://github.com/istupakov/onnx-asr),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Silero VAD](https://github.com/snakers4/silero-vad),
[eSpeak NG](https://github.com/espeak-ng/espeak-ng) y [Coqui XTTS](https://github.com/coqui-ai/TTS).
Lista completa con licencias: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Licencia

El código de este repositorio es MIT: ver [LICENSE](LICENSE). Ten en cuenta las licencias de las dependencias y modelos:

- `piper-tts` y eSpeak NG son **GPL-3.0**. Se instalan aparte; si distribuyes un paquete que los incluya, aplica la GPL.
- **XTTS v2** (backend opcional) usa la Coqui Public Model License: **solo uso no comercial**.
- Parakeet (CC-BY-4.0) y algunas voces Piper (CC-BY) exigen atribución.

Los modelos y voces no se incluyen en el repositorio: se descargan al usarlos.
