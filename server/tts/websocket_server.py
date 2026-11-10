"""
Servidor TTS WebSocket Multi-Backend - Servidor Streaming

Servidor de Text-to-Speech en tiempo real con:
- WebSocket para streaming de audio
- API REST para configuración
- Multiples backends TTS (Kokoro, Piper, XTTS) via TTSFactory
- Multiples formatos de audio
- Descarga automatica de voces
- Castellano nativo con Piper

Uso:
    python websocket_server.py
    TTS_BACKEND=piper python websocket_server.py   # forzar Piper
    TTS_BACKEND=auto python websocket_server.py   # autodetectar

Puerto: 8990
WebSocket: ws://localhost:8990/ws/tts
"""

import os
import json
import asyncio
import uuid
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Set
from contextlib import asynccontextmanager
import logging

os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = r"C:\Program Files\eSpeak NG\libespeak-ng.dll"
os.environ["PHONEMIZER_ESPEAK_PATH"] = r"C:\Program Files\eSpeak NG\espeak-ng.exe"

os.environ.setdefault(
    "PHONEMIZER_ESPEAK_LIBRARY", r"C:\Program Files\eSpeak NG\libespeak-ng.dll"
)
os.environ.setdefault(
    "PHONEMIZER_ESPEAK_PATH", r"C:\Program Files\eSpeak NG\espeak-ng.exe"
)

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    from uvicorn import run as uvicorn_run

    IMPORT_FASTAPI = True
except ImportError:
    IMPORT_FASTAPI = False
    print("WARNING: FastAPI not installed. Web server disabled.")
    print("Install with: pip install fastapi uvicorn[standard]")


from server.shared.config import settings
from server.shared.audio_converter import (
    AudioFormat,
    AudioConverter,
    create_audio_converter,
)
from server.shared.voice_downloader import create_voice_downloader
from server.shared.backends import TTSFactory, TTSBackend, BackendCapabilities
from server.shared.backends.base import VoiceInfo


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [trace=%(trace_id)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Inject the current trace_id into every log record.
import contextvars
_trace_id_var = contextvars.ContextVar("trace_id", default="-")


class _TraceIdFilter(logging.Filter):
    def filter(self, record):
        try:
            record.trace_id = _trace_id_var.get() or "-"
        except Exception:
            record.trace_id = "-"
        return True


for h in logging.getLogger().handlers:
    h.addFilter(_TraceIdFilter())


def _patch_middleware():
    """Sync the log trace_id to the TraceMiddleware's contextvar."""
    try:
        from server.shared.observability.context import trace_ctx_var
        import threading
        _stop = threading.Event()

        def _sync():
            try:
                ctx = trace_ctx_var.get()
                tid = ctx.get("trace_id", "-") if ctx else "-"
                _trace_id_var.set(tid or "-")
            except Exception:
                _trace_id_var.set("-")

        def _watcher():
            while not _stop.is_set():
                _sync()
                _stop.wait(0.05)

        threading.Thread(target=_watcher, daemon=True, name="trace-id-sync").start()
    except Exception:
        pass


_patch_middleware()


class ConnectionState:
    """Manages state for a single WebSocket connection."""

    def __init__(self, websocket: WebSocket, connection_id: str):
        self.websocket = websocket
        self.connection_id = connection_id
        self.voice = "es_f"
        self.speed = 1.0
        self.lang = "e"
        self.format = AudioFormat.PCM
        self.is_streaming = False
        self.current_request_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "voice": self.voice,
            "speed": self.speed,
            "lang": self.lang,
            "format": self.format.value,
            "is_streaming": self.is_streaming,
        }


class TTSServer:
    """
    TTS WebSocket Server con soporte multi-backend.

    Manages:
    - WebSocket connections
    - Multi-backend TTS (Kokoro, Piper, XTTS)
    - Voice generation
    - Audio streaming
    - LLM integration (optional)
    """

    def __init__(self, backend_name: Optional[str] = None):
        self.connections: Dict[str, ConnectionState] = {}
        self.backend: Optional[TTSBackend] = None
        self.backend_name = backend_name or settings.TTS_BACKEND
        self.audio_converter = create_audio_converter()
        self.voice_downloader = create_voice_downloader()
        self._generator_lock = asyncio.Lock()

        self.stats = {
            "total_requests": 0,
            "total_audio_chunks": 0,
            "total_audio_bytes": 0,
            "start_time": time.time(),
        }

    def initialize_generator(
        self,
        model_path: str = "kokoro.pth",
        voice_name: str = "es_f",
        init_timeout_s: float = 120.0,
    ):
        """
        Inicializa el backend TTS segun settings.TTS_BACKEND.
        SIEMPRE devuelve un TTSBackend (interfaz uniforme) - aunque sea kokoro.

        Args:
            model_path: ruta al modelo (solo kokoro).
            voice_name: voz inicial.
            init_timeout_s: timeout para la carga del modelo.
        """
        import concurrent.futures

        backend_name = self.backend_name
        if backend_name == "kokoro":
            # Wrapper KokoroBackend -> SIEMPRE devuelve TTSBackend interface
            self.backend = TTSFactory.create(
                "kokoro",
                models_dir=settings.MODELS_DIR,
                voices_dir=settings.VOICES_DIR,
            )
            # Si la voz no es formato Kokoro (no tiene "_"), usar default ef_dora
            if "_" not in voice_name:
                voice_name = "ef_dora"
            try:
                # Inicializar con timeout (cargar modelo puede colgarse)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(self.backend.initialize, model_path, voice_name)
                    try:
                        result = future.result(timeout=init_timeout_s)
                    except concurrent.futures.TimeoutError:
                        raise RuntimeError(
                            f"Timeout ({init_timeout_s}s) cargando modelo Kokoro "
                            f"({model_path}). Posible modelo colgado o disco lento."
                        )
            except (RuntimeError, FileNotFoundError) as e:
                err_msg = str(e).lower()
                if "voice" in err_msg or "not found" in err_msg or "no such" in err_msg:
                    if self.voice_downloader:
                        logger.info(f"Voice not found, attempting download: {voice_name}")
                        self.voice_downloader.download_voice(voice_name)
                        return self.backend.initialize(model_path, voice_name)
                raise
        else:
            # Multi-backend via TTSFactory
            if backend_name == "piper":
                model_dir = (
                    Path(settings.PIPER_VOICES_DIR)
                    if settings.PIPER_VOICES_DIR
                    else settings.BASE_DIR / "data" / "piper_voices"
                )
                self.backend = TTSFactory.create("piper", model_dir=model_dir)
                # Si voice_name no es formato piper (lang/voice-quality), usar default
                if "/" not in voice_name:
                    piper_default = "es_ES/davefx-medium"
                    voice_name = piper_default
                # Inicializar con timeout
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(self.backend.initialize, voice_name=voice_name)
                    try:
                        result = future.result(timeout=init_timeout_s)
                    except concurrent.futures.TimeoutError:
                        raise RuntimeError(
                            f"Timeout ({init_timeout_s}s) inicializando Piper "
                            f"({voice_name}). Posible descarga HF colgada."
                        )
                return result
            elif backend_name == "xtts":
                self.backend = TTSFactory.create("xtts", gpu=settings.XTTS_USE_GPU)
                return self.backend.initialize(voice_name=None)
            elif backend_name == "auto":
                self.backend = TTSFactory.create(
                    "auto",
                    models_dir=settings.MODELS_DIR,
                    voices_dir=settings.VOICES_DIR,
                    model_dir=settings.BASE_DIR / "data" / "piper_voices",
                )
                # Inicializar con voz default
                try:
                    return self.backend.initialize(voice_name=voice_name)
                except Exception:
                    return self.backend.initialize()
            else:
                raise ValueError(f"Backend '{backend_name}' no soportado")

    def get_voices_info(self) -> list:
        """Get info about all available voices en el backend activo."""
        if not self.backend or not self.backend.is_initialized():
            self.initialize_generator()

        # Si el backend implementa la interfaz TTSBackend (piper, xtts, kokoro nuevo)
        if hasattr(self.backend, "list_voices") and callable(self.backend.list_voices):
            # Verificar si devuelve List[VoiceInfo] o List[str]
            sample = self.backend.list_voices()
            if sample and hasattr(sample[0], "name"):
                # Es VoiceInfo
                return [
                    {
                        "name": v.name,
                        "lang_code": v.language,
                        "lang_name": v.language_name,
                        "gender": v.gender,
                        "quality": v.quality,
                        "description": v.description,
                        "is_default": v.name == "es_f" or v.name == "es_ES/davefx-medium",
                    }
                    for v in sample
                ]
        # Fallback: backend that returns List[str] (legacy Kokoro-style).
        # After Phase 8, the only active backend is Piper, which always
        # returns List[VoiceInfo]. This branch is kept defensively.
        LANGUAGE_MAP = {"a": "en-us", "b": "en-gb", "e": "es-es",
                        "i": "it-it", "f": "fr-fr", "j": "zh-cn",
                        "p": "pt-br", "d": "de-de", "k": "ko-kr", "r": "ru-ru"}
        voices = self.backend.list_available_voices()
        result = []
        for voice in voices:
            if isinstance(voice, str):
                lang_code = voice[0] if voice else "?"
                lang_name = LANGUAGE_MAP.get(lang_code, "unknown")
                gender = (
                    voice.split("_")[1][0]
                    if "_" in voice and len(voice.split("_")) > 1
                    else "?"
                )
                result.append({
                    "name": voice,
                    "lang_code": lang_code,
                    "lang_name": lang_name,
                    "gender": gender,
                    "is_default": voice == "es_f",
                })
            else:
                result.append({
                    "name": voice.name,
                    "lang_code": voice.language,
                    "lang_name": voice.language_name,
                    "gender": voice.gender,
                    "is_default": False,
                })
        return result

    def get_backend_info(self) -> dict:
        """Info del backend activo (capacidades)."""
        if not self.backend:
            return {"name": "none", "initialized": False}
        cap = self.backend.capabilities
        return {
            "name": self.backend.name,
            "initialized": self.backend.is_initialized(),
            "current_voice": self.backend.current_voice(),
            "sample_rate": self.backend.get_sample_rate(),
            "min_ram_gb": cap.min_ram_gb,
            "min_cpu_generation": cap.min_cpu_generation,
            "needs_gpu": cap.needs_gpu,
            "supports_streaming": cap.supports_streaming,
            "supports_voice_cloning": cap.supports_voice_cloning,
            "license": cap.license,
        }

    # TTS operational timeouts (seconds)
    SYNTHESIS_CHUNK_TIMEOUT_S = 10.0   # per-chunk synthesis (should be < 100ms usually)
    WEBSOCKET_SEND_TIMEOUT_S = 5.0     # send_bytes/json timeout
    TOTAL_TTS_TIMEOUT_S = 30.0         # total per-request cap

    @staticmethod
    def _estimate_word_offsets(text: str, total_samples: int, sample_rate: int) -> list:
        """Estimate per-word start offsets (ms) for protocol v2 highlights.

        Cheap heuristic: weight by word length (chars). Falls back to
        even spacing if the text is empty.
        """
        words = text.split()
        if not words:
            return []
        total_ms = total_samples / sample_rate * 1000.0
        weights = [max(1, len(w)) for w in words]
        total_weight = sum(weights)
        offsets = []
        cum = 0.0
        for w in weights:
            offsets.append(round(cum, 2))
            cum += (w / total_weight) * total_ms
        return offsets

    async def process_tts_request(
        self,
        state: ConnectionState,
        text: str,
        request_id: str,
        chunk_id: Optional[str] = None,
        protocol_version: int = 1,
    ):
        """Process a TTS request and stream audio chunks via backend.

        Protecciones:
          - Timeout total por request (TOTAL_TTS_TIMEOUT_S)
          - Timeout por chunk (SYNTHESIS_CHUNK_TIMEOUT_S)
          - Timeout por send al cliente (WEBSOCKET_SEND_TIMEOUT_S)
          - Aborta si el cliente se desconecta o pide interrupt

        Protocol v2 (protocol_version >= 2): emits audio_chunk JSON headers
        interleaved with binary PCM frames, plus an audio_end with
        word_offsets_ms so the client can highlight the spoken word.
        Falls back to v1 if the client didn't negotiate v2.
        """
        if not self.backend or not self.backend.is_initialized():
            await self._safe_send_json(
                state.websocket,
                {
                    "type": "error",
                    "code": "NOT_INITIALIZED",
                    "message": "Generator not initialized",
                },
            )
            return

        state.is_streaming = True
        state.current_request_id = request_id
        self.stats["total_requests"] += 1

        sample_rate = self.backend.get_sample_rate()
        t0 = time.time()
        use_v2 = protocol_version >= 2
        if chunk_id is None:
            chunk_id = f"c-{uuid.uuid4().hex[:8]}"
        try:
            if use_v2:
                # v2: send a pre-flight header so the client can allocate buffers
                # before the first PCM frame arrives. word_offsets_ms is filled
                # at audio_end time.
                await self._safe_send_json(
                    state.websocket,
                    {
                        "type": "audio_start",
                        "protocol_version": 2,
                        "request_id": request_id,
                        "chunk_id": chunk_id,
                        "voice": self.backend.current_voice() or state.voice,
                        "format": state.format.value,
                        "sample_rate": sample_rate,
                        "backend": self.backend.name,
                    },
                )
            else:
                await self._safe_send_json(
                    state.websocket,
                    {
                        "type": "audio_start",
                        "request_id": request_id,
                        "voice": self.backend.current_voice() or state.voice,
                        "format": state.format.value,
                        "sample_rate": sample_rate,
                        "backend": self.backend.name,
                    },
                )

            # Mover la generacion a un thread (piper bloquea) y consumir chunks con timeout
            import concurrent.futures
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as synth_ex:
                gen_iter = iter(
                    self.backend.generate_streaming(
                        text,
                        lang=state.lang,
                        speed=state.speed,
                        chunk_size=4800,
                    )
                )

                def _next_chunk():
                    try:
                        return next(gen_iter)
                    except StopIteration:
                        return None

                chunk_count = 0
                while True:
                    # Check total timeout
                    elapsed = time.time() - t0
                    if elapsed > self.TOTAL_TTS_TIMEOUT_S:
                        raise TimeoutError(
                            f"TTS request excedio {self.TOTAL_TTS_TIMEOUT_S}s "
                            f"({chunk_count} chunks enviados)"
                        )

                    # Pedir siguiente chunk al thread (timeout acotado)
                    remaining = self.TOTAL_TTS_TIMEOUT_S - elapsed
                    chunk_timeout = min(self.SYNTHESIS_CHUNK_TIMEOUT_S, remaining)
                    try:
                        chunk_result = await asyncio.wait_for(
                            loop.run_in_executor(synth_ex, _next_chunk),
                            timeout=chunk_timeout,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError(
                            f"Synthesis chunk {chunk_count} tardo > {chunk_timeout}s"
                        )

                    if chunk_result is None:
                        # Generator finished naturally
                        break

                    if state.current_request_id != request_id:
                        # Cliente pidio interrupt
                        break

                    chunk, is_final = chunk_result
                    if state.format == AudioFormat.PCM:
                        audio_bytes = chunk.tobytes()
                    else:
                        audio_bytes = self.audio_converter.convert(chunk, state.format)

                    self.stats["total_audio_chunks"] += 1
                    self.stats["total_audio_bytes"] += len(audio_bytes)

                    if use_v2:
                        # Emit a header before each PCM frame so the client
                        # can route the bytes back to the right chunk.
                        # Use a non-blocking send (best-effort; if it fails
                        # we still try to send the bytes).
                        await self._safe_send_json(
                            state.websocket,
                            {
                                "type": "audio_chunk",
                                "request_id": request_id,
                                "chunk_id": chunk_id,
                                "seq": chunk_count,
                                "sample_offset": chunk_count * 4800,
                                "samples": len(chunk),
                                "duration_ms": round(len(chunk) / sample_rate * 1000, 2),
                            },
                        )

                    try:
                        await asyncio.wait_for(
                            state.websocket.send_bytes(audio_bytes),
                            timeout=self.WEBSOCKET_SEND_TIMEOUT_S,
                        )
                    except asyncio.TimeoutError:
                        raise TimeoutError(
                            f"Cliente no consumio bytes en {self.WEBSOCKET_SEND_TIMEOUT_S}s"
                        )
                    except Exception as e:
                        # Cliente desconectado u otro error de socket
                        raise ConnectionError(f"WebSocket send fallo: {e}")

                    chunk_count += 1
                    if is_final:
                        if use_v2:
                            word_offsets_ms = self._estimate_word_offsets(text, chunk_count * 4800, sample_rate)
                            await self._safe_send_json(
                                state.websocket,
                                {
                                    "type": "audio_end",
                                    "protocol_version": 2,
                                    "request_id": request_id,
                                    "chunk_id": chunk_id,
                                    "final": True,
                                    "total_samples": chunk_count * 4800,
                                    "duration_ms": round(chunk_count * 4800 / sample_rate * 1000, 2),
                                    "word_offsets_ms": word_offsets_ms,
                                    "word_count": len(text.split()),
                                },
                            )
                        else:
                            await self._safe_send_json(
                                state.websocket,
                                {
                                    "type": "audio_end",
                                    "request_id": request_id,
                                    "final": True,
                                },
                            )
                        break

        except asyncio.TimeoutError as e:
            logger.error(f"TTS timeout: {e}")
            await self._safe_send_json(
                state.websocket,
                {
                    "type": "error",
                    "code": "TTS_TIMEOUT",
                    "message": str(e),
                },
            )
        except Exception as e:
            logger.error(f"TTS error: {e}")
            await self._safe_send_json(
                state.websocket,
                {
                    "type": "error",
                    "code": "TTS_ERROR",
                    "message": str(e),
                },
            )
        finally:
            state.is_streaming = False
            state.current_request_id = None

    async def _safe_send_json(self, ws: WebSocket, payload: dict):
        """send_json con timeout. No propaga excepciones (las loguea)."""
        try:
            await asyncio.wait_for(
                ws.send_json(payload),
                timeout=self.WEBSOCKET_SEND_TIMEOUT_S,
            )
        except Exception as e:
            logger.warning(f"safe_send_json fallo (cliente desconectado?): {e}")

    async def handle_websocket(self, websocket: WebSocket):
        """Handle a WebSocket connection."""
        connection_id = str(uuid.uuid4())[:8]
        state = ConnectionState(websocket, connection_id)
        self.connections[connection_id] = state

        logger.info(f"New connection: {connection_id}")

        try:
            await websocket.accept()

            if not self.backend or not self.backend.is_initialized():
                self.initialize_generator()

            # La voz de la conexión es la que tiene cargada el backend (el
            # valor por defecto "es_f" es de Kokoro y no existe en Piper).
            if self.backend and self.backend.is_initialized():
                state.voice = self.backend.current_voice() or state.voice

            # Protocol version comes from the very first message the client
            # sends (any type). Until then, default to v1 for backwards
            # compatibility. Once we receive any message with a
            # protocol_version field, we honor it.
            negotiated_protocol = 1
            await websocket.send_json(
                {
                    "type": "connected",
                    "connection_id": connection_id,
                    "voice": state.voice,
                    "available_voices": [v["name"] for v in self.get_voices_info()],
                    "backend": self.backend.name if self.backend else "none",
                    "sample_rate": self.backend.get_sample_rate() if self.backend else 24000,
                    "protocol_versions_supported": [1, 2],
                    "protocol_version": 1,
                }
            )

            while True:
                try:
                    data = await websocket.receive()

                    if "text" in data:
                        message = json.loads(data["text"])
                    elif "bytes" in data:
                        continue
                    else:
                        continue

                    msg_type = message.get("type")

                    if msg_type == "config":
                        state.voice = message.get("voice", state.voice)
                        state.speed = float(message.get("speed", state.speed))
                        state.lang = message.get("lang", state.lang)

                        if message.get("format"):
                            state.format = AudioFormat(message["format"])

                        # Cambiar voz en el backend activo solo si se pide una
                        # distinta (change_voice reinicializa el modelo).
                        try:
                            if (message.get("voice") and self.backend
                                    and self.backend.is_initialized()
                                    and state.voice != self.backend.current_voice()):
                                self.backend.change_voice(state.voice)
                        except FileNotFoundError:
                            if self.voice_downloader and self.backend_name == "kokoro":
                                self.voice_downloader.download_voice(state.voice)
                                self.backend.change_voice(state.voice)

                        await websocket.send_json(
                            {
                                "type": "config_ack",
                                "voice": state.voice,
                                "speed": state.speed,
                                "lang": state.lang,
                                "format": state.format.value,
                                "backend": self.backend.name if self.backend else "none",
                                "sample_rate": self.backend.get_sample_rate() if self.backend else 24000,
                            }
                        )

                    elif msg_type == "tts":
                        text = message.get("text", "")
                        request_id = message.get("request_id", str(uuid.uuid4()))
                        chunk_id = message.get("chunk_id")
                        if "protocol_version" in message:
                            negotiated_protocol = int(message["protocol_version"])

                        logger.info(
                            "TTS req id=%s chunk_id=%s proto=%d text=%r",
                            request_id, chunk_id, negotiated_protocol, text[:80],
                        )
                        if text.strip():
                            await self.process_tts_request(
                                state,
                                text,
                                request_id,
                                chunk_id=chunk_id,
                                protocol_version=negotiated_protocol,
                            )
                        else:
                            logger.warning("TTS req id=%s rejected: empty text", request_id)
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "code": "EMPTY_TEXT",
                                    "message": "Text is empty",
                                }
                            )

                    elif msg_type == "interrupt":
                        logger.info("interrupt id=%s (barge-in)", state.current_request_id)
                        state.current_request_id = None
                        state.is_streaming = False
                        await websocket.send_json(
                            {
                                "type": "interrupt_ack",
                            }
                        )

                    elif msg_type == "ping":
                        await websocket.send_json(
                            {
                                "type": "pong",
                                "timestamp": time.time(),
                            }
                        )

                    elif msg_type == "list_voices":
                        await websocket.send_json(
                            {
                                "type": "voices_list",
                                "voices": self.get_voices_info(),
                            }
                        )

                    elif msg_type == "download_voice":
                        voice_name = message.get("voice")
                        if voice_name and self.voice_downloader:
                            try:
                                self.voice_downloader.download_voice(voice_name)
                                await websocket.send_json(
                                    {
                                        "type": "download_complete",
                                        "voice": voice_name,
                                        "voices": self.get_voices_info(),
                                    }
                                )
                            except Exception as e:
                                await websocket.send_json(
                                    {
                                        "type": "error",
                                        "code": "DOWNLOAD_ERROR",
                                        "message": str(e),
                                    }
                                )

                except WebSocketDisconnect:
                    break
                except Exception as e:
                    logger.error(f"WebSocket error: {e}")
                    try:
                        await websocket.send_json(
                            {
                                "type": "error",
                                "code": "WEBSOCKET_ERROR",
                                "message": str(e),
                            }
                        )
                    except:
                        break

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"Connection error: {e}")
        finally:
            del self.connections[connection_id]
            logger.info(f"Connection closed: {connection_id}")

    def get_stats(self) -> dict:
        """Get server statistics."""
        return {
            **self.stats,
            "active_connections": len(self.connections),
            "uptime": time.time() - self.stats["start_time"],
        }


if IMPORT_FASTAPI:
    tts_server = TTSServer(backend_name=settings.TTS_BACKEND)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting TTS Server...")

        try:
            result = tts_server.initialize_generator(
                model_path=settings.TTS_MODEL,
                voice_name=settings.VOICE_NAME,
            )
            logger.info(f"Generator initialized: {result}")
        except Exception as e:
            logger.error(f"Failed to initialize generator: {e}")

        # Realtime voice: precargar whisper/Piper/VAD en segundo plano.
        import os
        if os.getenv("REALTIME_PRELOAD", "1") != "0":
            _realtime_ready["future"] = asyncio.get_running_loop().run_in_executor(None, preload_realtime)

        yield

        logger.info("Shutting down TTS Server...")

    app = FastAPI(
        title="Kokoro TTS WebSocket Server",
        description="Servidor de Text-to-Speech en tiempo real con streaming WebSocket",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Observability: trace context + span per request
    from server.shared.observability import TraceMiddleware
    app.add_middleware(TraceMiddleware)

    # API key (solo si API_KEYS está definida; ver server/shared/auth.py).
    # Va antes de CORS para que CORS quede por fuera: el preflight pasa y los
    # 401 llevan cabeceras CORS.
    from server.shared.auth import APIKeyMiddleware
    app.add_middleware(APIKeyMiddleware)

    # CORS: solo origenes locales + file:// por defecto (ver server/shared/net.py)
    from server.shared.net import cors_kwargs
    app.add_middleware(CORSMiddleware, **cors_kwargs())

    @app.get("/")
    async def root():
        return {
            "name": "Kokoro TTS WebSocket Server",
            "version": "1.0.0",
            "websocket": "/ws/tts",
            "docs": "/docs",
        }

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        return {
            "status": "ok",
            "backend": tts_server.backend.name if tts_server.backend else "none",
            "generator_ready": tts_server.backend is not None
            and tts_server.backend.is_initialized(),
            "stats": tts_server.get_stats(),
        }

    @app.get("/metrics")
    async def metrics():
        """Return the current snapshot of metrics + recent traces."""
        from server.shared.observability import get_registry, get_tracer
        return {
            "metrics": get_registry().snapshot(),
            "recent_traces": get_tracer().get_collector().recent(20),
        }

    # ---- LLM proxy (CORS workaround) ----
    # The browser can't call LM Studio directly (port 12344 has no CORS
    # headers). We proxy /llm/* to the upstream LLM, adding the
    # appropriate CORS headers on the way out. Streaming is supported.

    # Active LLM runs (P0.4 — P0.5). Keyed by X-Run-Id. The X-Run-Id
    # is propagated by the client so the proxy can fence stale events
    # against a specific run. When the client aborts the fetch, we close
    # the upstream HTTPX stream, which causes is_disconnected() in
    # the FastAPI/Starlette request lifecycle to fire.
    _active_runs: dict[str, asyncio.Event] = {}
    _active_runs_lock = asyncio.Lock()

    @app.api_route("/llm/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    async def llm_proxy(path: str, request: Request):
        import httpx
        from server.shared.observability import get_tracer, get_registry

        run_id = request.headers.get("X-Run-Id") or f"run-{uuid.uuid4().hex[:8]}"
        cancel_event = asyncio.Event()
        async with _active_runs_lock:
            _active_runs[run_id] = cancel_event

        reg = get_registry()
        upstream_cancelled = reg.counter("llm_upstream_cancelled_total", {"reason": "client_disconnect"})
        upstream_completed = reg.counter("llm_upstream_completed_total")
        llm_cancel_total = reg.counter("llm_cancel_total")
        llm_cancel_latency = reg.histogram("llm_cancel_to_disconnect_ms")

        base = settings.LM_STUDIO_URL.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        target = f"{base}/{path}"
        if request.url.query:
            target += f"?{request.url.query}"

        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "origin", "referer", "x-run-id")
        }
        fwd_headers["X-Run-Id"] = run_id

        body = await request.body()
        method = request.method
        is_streaming = "text/event-stream" in (request.headers.get("accept") or "")
        logger.info(
            "[llm-proxy] run=%s %s %s -> %s stream=%s",
            run_id, method, path, target, is_streaming,
        )

        tracer = get_tracer()
        with tracer.span(f"llm-proxy {method} /{path}", {"upstream": base, "run_id": run_id}) as span:
            t0 = time.time()
            try:
                if is_streaming:
                    # Streaming mode (SSE / chunked): forward each chunk
                    # as it arrives, allowing mid-stream cancel.
                    async def stream_gen():
                        try:
                            async with httpx.AsyncClient(
                                timeout=httpx.Timeout(60.0, connect=10.0)
                            ) as client:
                                req = client.build_request(
                                    method=method, url=target,
                                    headers=fwd_headers, content=body,
                                )
                                async with client.stream(method, target, headers=fwd_headers, content=body) as upstream:
                                    upstream.raise_for_status()
                                    async for chunk in upstream.aiter_bytes():
                                        if cancel_event.is_set():
                                            upstream_cancelled.inc()
                                            llm_cancel_total.inc()
                                            llm_cancel_latency.observe(
                                                (time.time() - t0) * 1000
                                            )
                                            break
                                        if await request.is_disconnected():
                                            upstream_cancelled.inc()
                                            llm_cancel_total.inc()
                                            llm_cancel_latency.observe(
                                                (time.time() - t0) * 1000
                                            )
                                            break
                                        yield chunk
                        finally:
                            async with _active_runs_lock:
                                _active_runs.pop(run_id, None)
                    return StreamingResponse(
                        stream_gen(),
                        media_type="text/event-stream",
                        headers={"X-Run-Id": run_id},
                    )
                else:
                    # Non-streaming: read the full response, then return.
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(60.0, connect=10.0)
                    ) as client:
                        req = client.build_request(
                            method=method, url=target, headers=fwd_headers, content=body
                        )
                        upstream_resp = await client.send(req, stream=False)
            except httpx.HTTPError as exc:
                logger.error("LLM proxy upstream error: %s", exc)
                span.set_status("error")
                return JSONResponse(
                    {"error": f"upstream_unreachable: {exc!s}"},
                    status_code=502,
                )

            span.set_attribute("http.status_code", upstream_resp.status_code)
            upstream_completed.inc()
            response_headers = {
                k: v for k, v in upstream_resp.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding", "connection")
            }
            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=response_headers,
                media_type=upstream_resp.headers.get("content-type"),
            )

    @app.get("/session/state")
    async def session_state():
        """Return the current SessionState (Phase 7)."""
        from server.shared.session import get_session_state
        return get_session_state().to_dict()

    @app.get("/voices")
    async def list_voices():
        """List all available voices."""
        return {"voices": tts_server.get_voices_info()}

    @app.get("/voices/{name}")
    async def get_voice(name: str):
        """Get info about a specific voice."""
        voices = tts_server.get_voices_info()
        for v in voices:
            if v["name"] == name:
                return v
        raise HTTPException(status_code=404, detail=f"Voice '{name}' not found")

    @app.post("/voices/download/{name}")
    async def download_voice(name: str):
        """Download a voice from HuggingFace."""
        if not tts_server.voice_downloader:
            raise HTTPException(status_code=501, detail="Downloader not available")

        try:
            tts_server.voice_downloader.download_voice(name)
            return {
                "status": "success",
                "voice": name,
                "voices": tts_server.get_voices_info(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/voices/download/all")
    async def download_all_voices():
        """Download all available voices."""
        if not tts_server.voice_downloader:
            raise HTTPException(status_code=501, detail="Downloader not available")

        result = tts_server.voice_downloader.download_all()
        return {"status": "complete", "result": result}

    @app.get("/config")
    async def get_config():
        """Get current server configuration."""
        voice = "es_f"
        lang = "e"
        sample_rate = 24000
        if tts_server.backend and tts_server.backend.is_initialized():
            voice = tts_server.backend.current_voice() or "es_f"
            try:
                lang = tts_server.backend.get_language() if hasattr(tts_server.backend, "get_language") else "e"
            except Exception:
                lang = "e"
            sample_rate = tts_server.backend.get_sample_rate()
        return {
            "voice": voice,
            "lang": lang,
            "speed": 1.0,
            "format": "pcm",
            "sample_rate": sample_rate,
            "backend": tts_server.backend.name if tts_server.backend else "none",
        }

    @app.get("/backend")
    async def get_backend():
        """Info del backend TTS activo (capacidades, voz actual, etc)."""
        return tts_server.get_backend_info()

    @app.get("/stats")
    async def get_stats():
        """Get server statistics."""
        return tts_server.get_stats()

    # ---- TTS por HTTP (curl, scripts, otros servicios) ----
    # El WebSocket /ws/tts es para streaming interactivo; esto es para
    # consumir el servicio con una sola peticion:
    #   curl -X POST http://127.0.0.1:8990/tts -H "Content-Type: application/json" \
    #        -d '{"text":"Hola"}' -o hola.wav
    # /v1/audio/speech acepta el formato de OpenAI ({"input", "voice", "speed",
    # "response_format"}) para usar SDKs de OpenAI apuntando a este servidor.
    _http_tts_lock = threading.Lock()

    def _http_tts_synthesize(text: str, voice: Optional[str], speed: float) -> tuple:
        """Sintetiza completo en un hilo. Devuelve (pcm16_bytes, sample_rate, voz)."""
        import numpy as np
        backend = tts_server.backend
        with _http_tts_lock:
            if voice and voice != backend.current_voice():
                backend.change_voice(voice)
            parts = [np.asarray(c, dtype=np.float32)
                     for c, _last in backend.generate_streaming(text, speed=speed, chunk_size=4800)
                     if c is not None and len(c)]
            used_voice = backend.current_voice()
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        return pcm, int(backend.get_sample_rate()), used_voice

    def _wav_bytes(pcm16: bytes, sample_rate: int) -> bytes:
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm16)
        return buf.getvalue()

    def _wav_stream_header(sample_rate: int) -> bytes:
        """Cabecera WAV PCM16 mono con tamaños 'desconocidos' (0xFFFFFFFF) para
        streaming: ffplay, navegadores y la mayoría de reproductores la aceptan."""
        import struct
        return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
                + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
                + b"data" + struct.pack("<I", 0xFFFFFFFF))

    async def _http_tts_stream(text: str, voice: Optional[str], speed: float, fmt: str) -> Response:
        """Envía el audio a medida que Piper lo genera (chunked), sin esperar al
        final: el cliente puede reproducir mientras llega. Si el cliente corta,
        la síntesis se detiene en el siguiente chunk."""
        import numpy as np
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        cancelled = threading.Event()
        put = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)  # noqa: E731

        def worker():
            backend = tts_server.backend
            try:
                with _http_tts_lock:
                    if voice and voice != backend.current_voice():
                        backend.change_voice(voice)
                    put(("start", int(backend.get_sample_rate()), backend.current_voice()))
                    for chunk, _last in backend.generate_streaming(text, speed=speed, chunk_size=4800):
                        if cancelled.is_set():
                            break
                        if chunk is None or not len(chunk):
                            continue
                        a = np.clip(np.asarray(chunk, dtype=np.float32), -1.0, 1.0)
                        put(("data", (a * 32767.0).astype("<i2").tobytes()))
            except Exception as e:  # noqa: BLE001
                put(("error", e))
            finally:
                put(("end",))

        t0 = time.time()
        loop.run_in_executor(None, worker)
        first = await queue.get()
        if first[0] == "error":
            await queue.get()  # "end"
            logger.error("HTTP TTS stream fallo: %s", first[1])
            raise HTTPException(status_code=500, detail=f"Fallo la sintesis: {first[1]}")
        _, sr, used_voice = first
        tts_server.stats["total_requests"] += 1

        async def body():
            sent = 0
            try:
                if fmt == "wav":
                    yield _wav_stream_header(sr)
                while True:
                    item = await queue.get()
                    if item[0] == "data":
                        sent += len(item[1])
                        yield item[1]
                    elif item[0] == "error":
                        logger.error("HTTP TTS stream fallo a mitad: %s", item[1])
                    elif item[0] == "end":
                        break
            finally:
                cancelled.set()
                tts_server.stats["total_audio_bytes"] += sent
                logger.info("HTTP TTS stream voz=%s %d chars -> %.0f ms de audio en %.0f ms",
                            used_voice, len(text), sent / 2 / sr * 1000.0, (time.time() - t0) * 1000.0)

        headers = {"X-Sample-Rate": str(sr), "X-Voice": str(used_voice or ""),
                   "Cache-Control": "no-store"}
        media = "audio/wav" if fmt == "wav" else "audio/L16"
        return StreamingResponse(body(), media_type=media, headers=headers)

    async def _http_tts(request: Request, text_key: str, format_key: str) -> Response:
        raw = await request.body()
        try:
            try:
                body = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                # curl en la consola de Windows manda los acentos en cp1252.
                body = json.loads(raw.decode("cp1252"))
        except Exception:
            raise HTTPException(status_code=400, detail="El cuerpo debe ser JSON (UTF-8)")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="El cuerpo debe ser un objeto JSON")
        text = str(body.get(text_key) or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail=f"Falta '{text_key}' (texto a sintetizar)")
        if len(text) > 5000:
            raise HTTPException(status_code=413, detail="Texto demasiado largo (max 5000 caracteres)")
        fmt = str(body.get(format_key) or "wav").lower()
        if fmt not in ("wav", "pcm"):
            raise HTTPException(status_code=400, detail="Formato no soportado: usa 'wav' o 'pcm'")
        try:
            speed = float(body.get("speed", settings.SPEED))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="'speed' debe ser numerico")
        speed = min(max(speed, 0.25), 4.0)
        voice = body.get("voice") or None
        stream = bool(body.get("stream", False))

        if tts_server.backend is None or not tts_server.backend.is_initialized():
            raise HTTPException(status_code=503, detail="Backend TTS no inicializado")
        # Las voces de OpenAI (alloy, nova...) no existen aqui: se usa la actual.
        if voice and tts_server.backend.get_voice(voice) is None:
            if text_key == "input":
                voice = None
            else:
                raise HTTPException(status_code=404, detail=f"Voz desconocida: {voice} (ver GET /voices)")

        if stream:
            return await _http_tts_stream(text, voice, speed, fmt)

        t0 = time.time()
        try:
            pcm, sr, used_voice = await asyncio.to_thread(_http_tts_synthesize, text, voice, speed)
        except Exception as e:
            logger.exception("HTTP TTS fallo")
            raise HTTPException(status_code=500, detail=f"Fallo la sintesis: {e}")
        synth_ms = (time.time() - t0) * 1000.0
        tts_server.stats["total_requests"] += 1
        tts_server.stats["total_audio_bytes"] += len(pcm)
        duration_ms = len(pcm) / 2 / sr * 1000.0 if sr else 0.0
        logger.info("HTTP TTS voz=%s %d chars -> %.0f ms de audio en %.0f ms",
                    used_voice, len(text), duration_ms, synth_ms)
        headers = {
            "X-Sample-Rate": str(sr),
            "X-Voice": str(used_voice or ""),
            "X-Audio-Duration-Ms": f"{duration_ms:.0f}",
            "X-Synthesis-Ms": f"{synth_ms:.0f}",
        }
        if fmt == "pcm":
            return Response(pcm, media_type="audio/L16", headers=headers)
        return Response(_wav_bytes(pcm, sr), media_type="audio/wav", headers=headers)

    @app.post("/tts")
    async def http_tts(request: Request):
        """JSON {text, voice?, speed?, format?: wav|pcm} -> audio WAV PCM16 mono."""
        return await _http_tts(request, "text", "format")

    @app.post("/v1/audio/speech")
    async def openai_speech(request: Request):
        """Compatible con OpenAI: {input, voice?, speed?, response_format?: wav|pcm}."""
        return await _http_tts(request, "input", "response_format")

    @app.websocket("/ws/runs/{run_id}/cancel")
    async def cancel_run_ws(websocket: WebSocket, run_id: str):
        """Control plane: explicitly cancel a running LLM run.

        This is an alternative to closing the LLM fetch (which would
        trigger is_disconnected on the proxy). Used when the client
        wants to ensure the upstream is torn down even if the fetch
        connection is somehow kept alive.
        """
        await websocket.accept()
        async with _active_runs_lock:
            event = _active_runs.get(run_id)
        if event is not None:
            event.set()
            await websocket.send_json({"status": "cancelling", "run_id": run_id})
        else:
            await websocket.send_json({"status": "not_found", "run_id": run_id})
        await websocket.close()

    @app.websocket("/ws/tts")
    async def websocket_tts(websocket: WebSocket):
        await tts_server.handle_websocket(websocket)

    # ---- Realtime voice (estilo OpenAI Realtime, 100% local) ----
    # Un solo WS full-duplex: PCM16 del mic -> VAD/STT/LLM/TTS en servidor
    # -> PCM16 + eventos. Ver server/realtime/ y client/sdk/.
    _realtime_engines: dict = {}
    _realtime_ready: dict = {}  # {"future": asyncio.Future del preload}
    _realtime_lock = threading.Lock()

    def realtime_engines() -> dict:
        """Crea los motores una vez (thread-safe) y carga la sesion ONNX del VAD."""
        with _realtime_lock:
            return _realtime_engines_locked()

    def _realtime_engines_locked() -> dict:
        from server.realtime.vad import _silero_session
        _silero_session()
        if not _realtime_engines:
            from server.realtime.engines import OpenAICompatLLM, stt_from_env
            _realtime_engines["stt"] = stt_from_env()
            _realtime_engines["llm"] = OpenAICompatLLM(settings.LM_STUDIO_URL, settings.LLM_MODEL)
        if tts_server.backend is None or not tts_server.backend.is_initialized():
            tts_server.initialize_generator()
        from server.realtime.engines import BackendTTS
        _realtime_engines["tts"] = BackendTTS(tts_server.backend)
        return _realtime_engines

    def preload_realtime() -> None:
        """Carga y calienta STT + Piper para que el primer turno sea rapido."""
        try:
            eng = realtime_engines()
            eng["stt"].warmup()
            if hasattr(tts_server.backend, "warmup"):
                tts_server.backend.warmup()
            logger.info("Realtime voice listo (STT %s)", eng["stt"].model_name)
        except Exception as e:
            logger.warning("Realtime preload fallo: %s", e)
        _warmup_llm()

    def _warmup_llm() -> None:
        """Pide 1 token a LM Studio: con JIT, carga el modelo y cachea el prompt
        de sistema antes del primer turno (si no, el primero tarda ~2 s mas)."""
        import httpx
        from server.realtime.session import VOICE_STYLE
        llm = _realtime_engines.get("llm")
        if llm is None:
            return
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": settings.DEFAULT_SYSTEM_PROMPT.rstrip() + VOICE_STYLE},
                {"role": "user", "content": "Hola"},
            ],
            "max_tokens": 1,
            "stream": False,
        }
        if llm.reasoning_effort:
            payload["reasoning_effort"] = llm.reasoning_effort
        try:
            t0 = time.time()
            httpx.post(llm.url, json=payload, timeout=120).raise_for_status()
            logger.info("Realtime LLM listo (%s, %.1f s)", settings.LLM_MODEL, time.time() - t0)
        except Exception as e:
            logger.warning("Warmup del LLM fallo (LM Studio apagado?): %s", e)

    @app.websocket("/ws/voice")
    async def websocket_voice(websocket: WebSocket):
        from server.realtime.session import SessionConfig, VOICE_STYLE
        from server.realtime.ws import serve_websocket
        loop = asyncio.get_running_loop()
        pending = _realtime_ready.get("future")
        if pending is not None and not pending.done():
            # Evita importar faster_whisper/onnx en paralelo con el preload
            # (deadlock del import lock de Python).
            await asyncio.shield(pending)
        eng = await loop.run_in_executor(None, realtime_engines)
        cfg = SessionConfig(
            instructions=settings.DEFAULT_SYSTEM_PROMPT.rstrip() + VOICE_STYLE,
            model=settings.LLM_MODEL,
            temperature=settings.LLM_TEMPERATURE,
            speed=settings.SPEED,
        )
        await serve_websocket(websocket, stt=eng["stt"], llm=eng["llm"], tts=eng["tts"], config=cfg)

    def run_server(host: str | None = None, port: int = 8990):
        """Run the FastAPI server (host defaults to BIND_HOST / 127.0.0.1)."""
        from server.shared.net import bind_host
        uvicorn_run(
            app,
            host=host or bind_host(),
            port=port,
            log_level="info",
        )


def main():
    print("=" * 60)
    print("  TTS WebSocket Server (Multi-Backend)")
    print("=" * 60)
    print()
    print(f"  Puerto WebSocket: 8990")
    print(f"  WebSocket URL: ws://localhost:8990/ws/tts")
    print(f"  API Docs: http://localhost:8990/docs")
    print()
    print(f"  Backend TTS: {settings.TTS_BACKEND}")
    if settings.TTS_BACKEND == "piper":
        print(f"  Castellano nativo: es_ES")
    elif settings.TTS_BACKEND == "kokoro":
        print(f"  Castellano: solo acento latino (3 voces)")
    print()
    print("=" * 60)
    print()

    if IMPORT_FASTAPI:
        run_server(port=8990)
    else:
        print("ERROR: FastAPI not installed.")
        print("Install with: pip install fastapi uvicorn[standard]")
        exit(1)


if __name__ == "__main__":
    main()
