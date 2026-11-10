"""
STT Microservice - Speech-to-Text via faster-whisper.

Endpoint desacoplado. El cliente (browser, desktop, mobile) captura audio
y lo POSTea aqui. Este server lo transcribe y devuelve texto.

Stack:
  - FastAPI: HTTP server
  - faster-whisper: STT engine (4x mas rapido que openai-whisper en CPU)
  - Uvicorn: ASGI runner

Uso:
  python stt_server.py
  python stt_server.py --port 8000 --model tiny --device cpu
  python stt_server.py --port 8000 --model base --device cuda

Endpoints:
  GET  /health           - Health check (incluye modelo cargado)
  GET  /models           - Lista de modelos disponibles
  POST /transcribe       - body: multipart/form-data con field "audio" (WAV 16kHz mono, max 30s)
                           query params: ?language=es&model=tiny&beam_size=5

Modelo por default: 'tiny' (200MB RAM, carga ~3s, ~0.5s para 5s de audio en CPU i7)

NOTA SOBRE EL ACOPLAMIENTO:
  Este servicio es INDEPENDIENTE del LLM, TTS y del cliente.
  Solo recibe audio y devuelve texto. No sabe quien lo llama.
  Cualquier frontend puede usarlo: browser (fetch), Electron, mobile, etc.
"""
import os
import sys
import io
import time
import logging
import argparse
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [trace=%(trace_id)s] %(name)s: %(message)s",
)
logger = logging.getLogger("stt_server")

# Inject the current trace_id into every log record so the format above works.
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


# Hook into the TraceMiddleware contextvar (set by FastAPI middleware).
def _patch_middleware():
    try:
        from server.shared.observability.context import trace_ctx_var
        # Wire our log trace_id to the middleware's trace context.
        def _sync():
            try:
                tid = trace_ctx_var.get().get("trace_id", "-") if trace_ctx_var.get() else "-"
                _trace_id_var.set(tid or "-")
            except Exception:
                _trace_id_var.set("-")
        import threading
        _stop = threading.Event()
        def _watcher():
            while not _stop.is_set():
                _sync()
                _stop.wait(0.05)
        t = threading.Thread(target=_watcher, daemon=True, name="trace-id-sync")
        t.start()
    except Exception:
        pass

_patch_middleware()


# === GLOBAL STATE ===
# Cache de modelos cargados (lazy load). Whisper tarda en cargar.
# Default: Parakeet TDT 0.6B v3 int8 (onnx-asr). En español: WER 2.5 % frente
# al 26 % de whisper tiny, y ~260 ms por frase en CPU (ver docs/REALTIME.md).
# STT_MODEL=base|small|... para volver a whisper.
_MODEL_CACHE: dict = {}
_DEFAULT_MODEL_NAME: str = os.getenv("STT_MODEL", "parakeet")
if _DEFAULT_MODEL_NAME == "parakeet":
    import importlib.util
    if importlib.util.find_spec("onnx_asr") is None:
        _DEFAULT_MODEL_NAME = "base"  # sin onnx-asr: whisper base (beam 5)
_DEFAULT_DEVICE: str = "cpu"
_DEFAULT_COMPUTE_TYPE: str = "int8"  # CPU int8 = mas rapido, ~mismo accuracy
_MAX_AUDIO_BYTES: int = 10 * 1024 * 1024  # 10MB de audio max (suficiente para 60s @ 16kHz)
_MAX_AUDIO_DURATION_S: float = 60.0
PARAKEET = "parakeet"
_AVAILABLE_MODELS = [PARAKEET, "tiny", "tiny.en", "base", "base.en", "small", "medium", "large-v3", "turbo"]


def get_model(model_name: str, device: str, compute_type: str):
    """Carga (o recupera de cache) un modelo faster-whisper."""
    if model_name == PARAKEET:
        key = (PARAKEET, "cpu", "int8")
        if key not in _MODEL_CACHE:
            from server.realtime.engines import ParakeetSTT
            t0 = time.time()
            stt = ParakeetSTT(quantization=os.getenv("STT_PARAKEET_QUANT", "int8") or None)
            stt.warmup()
            _MODEL_CACHE[key] = stt
            logger.info(f"Modelo parakeet cargado en {time.time()-t0:.1f}s")
        return _MODEL_CACHE[key]
    key = (model_name, device, compute_type)
    if key not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        logger.info(f"Cargando modelo {model_name} (device={device}, compute={compute_type})...")
        t0 = time.time()
        _MODEL_CACHE[key] = WhisperModel(model_name, device=device, compute_type=compute_type)
        logger.info(f"Modelo {model_name} cargado en {time.time()-t0:.1f}s")
    return _MODEL_CACHE[key]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Carga el modelo default al startup. No bloquea si falla."""
    logger.info("STT server iniciando...")
    try:
        # Carga eager del default para que /health responda model_loaded=True rapido
        get_model(_DEFAULT_MODEL_NAME, _DEFAULT_DEVICE, _DEFAULT_COMPUTE_TYPE)
    except Exception as e:
        logger.error(f"No se pudo cargar el modelo default al startup: {e}")
        logger.error("El servicio iniciara de todas formas; el modelo se cargara lazy en el primer request.")
    yield
    logger.info("STT server cerrando.")


# === APP ===
app = FastAPI(
    title="STT Microservice",
    description="Speech-to-Text via faster-whisper. Desacoplado del LLM/TTS.",
    version="1.0.0",
    lifespan=lifespan,
)

# Observability: trace context + span per request
from server.shared.observability import TraceMiddleware
app.add_middleware(TraceMiddleware)

# CORS: solo origenes locales + file:// por defecto (ver server/shared/net.py)
from server.shared.net import cors_kwargs, bind_host
app.add_middleware(CORSMiddleware, **cors_kwargs())


# === ENDPOINTS ===

@app.get("/")
async def root():
    return {
        "name": "STT Microservice",
        "version": "1.0.0",
        "engine": "faster-whisper",
        "endpoints": {
            "GET /health": "Health check + modelo cargado",
            "GET /models": "Modelos disponibles",
            "POST /transcribe": "Subir audio (WAV 16kHz mono), devuelve texto",
            "GET /metrics": "Snapshot of observability metrics",
        },
    }


@app.get("/metrics")
async def metrics():
    """Return the current snapshot of all metrics for inspection."""
    from server.shared.observability import get_registry, get_tracer
    return {
        "metrics": get_registry().snapshot(),
        "recent_traces": get_tracer().get_collector().recent(20),
    }


@app.get("/session/state")
async def session_state():
    """Return the current SessionState (Phase 7)."""
    from server.shared.session import get_session_state
    return get_session_state().to_dict()


@app.get("/health")
async def health():
    """Health check. Devuelve estado del modelo default."""
    default_key = (_DEFAULT_MODEL_NAME, _DEFAULT_DEVICE, _DEFAULT_COMPUTE_TYPE)
    return {
        "status": "ok",
        "model_loaded": default_key in _MODEL_CACHE,
        "default_model": _DEFAULT_MODEL_NAME,
        "device": _DEFAULT_DEVICE,
        "compute_type": _DEFAULT_COMPUTE_TYPE,
        "models_in_cache": [list(k) for k in _MODEL_CACHE.keys()],
        "uptime_s": time.time() - _START_TIME,
    }


@app.get("/models")
async def list_models():
    """Lista de modelos que faster-whisper puede cargar."""
    return {
        "available": _AVAILABLE_MODELS,
        "loaded": [list(k) for k in _MODEL_CACHE.keys()],
        "default": _DEFAULT_MODEL_NAME,
        "descriptions": {
            "tiny": "75M params, 200MB RAM, ~0.5s para 5s audio en CPU",
            "base": "74M params, ~300MB RAM, mejor calidad que tiny",
            "small": "244M params, ~1GB RAM, recomendado para produccion",
            "medium": "769M params, ~3GB RAM, alta calidad",
            "large-v3": "1550M params, ~6GB RAM, maxima calidad",
            "turbo": "809M params, optimizado para velocidad",
            "parakeet": "NVIDIA Parakeet TDT 0.6B v3 int8 (ONNX), 25 idiomas; el mas preciso y rapido en CPU",
        },
    }


# ---- Result-returning helpers (Phase 6) ----

def _decode_audio(audio_bytes: bytes):
    """Decode WAV/PCM bytes into a (audio_data, sample_rate, duration_s) tuple.

    Returns Ok(tuple) on success or Err((status_code, detail)) on failure.
    """
    from server.shared.result import Ok, Err, of as _of
    import numpy as np
    import soundfile as sf

    class _DomainError(Exception):
        """Carries a (status_code, detail) tuple as its single argument."""
        def __init__(self, status: int, detail: str):
            super().__init__(detail)
            self.status = status
            self.detail = detail

    def _do_decode():
        # soundfile supports WAV, FLAC, OGG. WebM/Opus would need ffmpeg.
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)
        if sample_rate != 16000:
            import scipy.signal
            audio_data = scipy.signal.resample(
                audio_data,
                int(len(audio_data) * 16000 / sample_rate),
            ).astype(np.float32)
            sample_rate = 16000
        duration_s = len(audio_data) / sample_rate
        if duration_s > _MAX_AUDIO_DURATION_S:
            raise _DomainError(
                413,
                f"Audio demasiado largo ({duration_s:.1f}s, max {_MAX_AUDIO_DURATION_S})",
            )
        return audio_data, sample_rate, duration_s

    res = _of(_do_decode)
    if res.is_err():
        e = res.error
        if isinstance(e, _DomainError):
            return Err((e.status, e.detail))
        return Err((400, f"Error decodificando audio: {e}"))
    return Ok(res.value)


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(..., description="Audio WAV/PCM 16kHz mono. Max 60s."),
    language: Optional[str] = Form(default=None, description="Codigo ISO: 'es', 'en', etc. Auto-detecta si None."),
    model: str = Form(default="", description="'parakeet' o modelo Whisper; vacio = default del servidor"),
    device: str = Form(default="cpu"),
    compute_type: str = Form(default="int8"),
    beam_size: int = Form(default=5, ge=1, le=10),
    vad_filter: bool = Form(default=True, description="Filtrar silencios con VAD antes de transcribir"),
):
    """
    Transcribe audio a texto.

    Acepta: multipart/form-data con field "audio" (file)
    Opcionales: language, model, device, compute_type, beam_size, vad_filter

    Retorna:
      {
        "text": "...",
        "language": "es",
        "language_probability": 0.99,
        "duration_s": 4.2,
        "model": "tiny",
        "elapsed_s": 0.5
      }
    """
    model = model or _DEFAULT_MODEL_NAME
    if model not in _AVAILABLE_MODELS:
        from server.shared.observability import get_registry
        get_registry().counter("stt.errors", {"model": model, "code": "invalid_model"}).inc()
        raise HTTPException(status_code=400, detail=f"Modelo '{model}' no valido. Disponibles: {_AVAILABLE_MODELS}")

    from server.shared.observability import get_registry, get_tracer
    from server.shared.result import of_async, of as _of
    reg = get_registry()
    tracer = get_tracer()
    req_counter = reg.counter("stt.transcribe.requests", {"model": model})
    err_counter = reg.counter("stt.transcribe.errors", {"model": model})
    elapsed_hist = reg.histogram("stt.transcribe.elapsed_ms", {"model": model})
    rtf_hist = reg.histogram("stt.transcribe.rtf", {"model": model})
    req_counter.inc()
    t_start = time.time()

    with tracer.span("stt.transcribe", {"model": model, "language": language or "auto"}):
        # 1. Leer audio — wrap in Result so the failure is a value.
        bytes_res = await of_async(audio.read())
        if bytes_res.is_err():
            err_counter.inc()
            raise HTTPException(status_code=500, detail=f"Error leyendo audio: {bytes_res.error}")
        audio_bytes = bytes_res.value
        if len(audio_bytes) == 0:
            err_counter.inc()
            raise HTTPException(status_code=400, detail="Audio vacio")
        if len(audio_bytes) > _MAX_AUDIO_BYTES:
            err_counter.inc()
            raise HTTPException(
                status_code=413,
                detail=f"Audio demasiado grande ({len(audio_bytes)} bytes, max {_MAX_AUDIO_BYTES})"
            )
        logger.info(
            "STT /transcribe received: %d bytes, filename='%s', model=%s, language=%s",
            len(audio_bytes), audio.filename, model, language or "auto",
        )
        reg.histogram("stt.audio.bytes", {"model": model}).observe(len(audio_bytes))

    # 2. Decodificar WAV/PCM — wrap in Result.
    decode_res = _decode_audio(audio_bytes)
    if decode_res.is_err():
        err_counter.inc()
        status, detail = decode_res.error
        raise HTTPException(status_code=status, detail=detail)
    audio_data, sample_rate, duration_s = decode_res.value
    logger.info(f"Audio decodificado: {duration_s:.2f}s @ {sample_rate}Hz")
    reg.histogram("stt.audio.duration_s", {"model": model}).observe(duration_s)

    # 3. Cargar modelo — wrap in Result.
    model_res = _of(lambda: get_model(model, device, compute_type))
    if model_res.is_err():
        err_counter.inc()
        logger.error(f"Error cargando modelo {model}: {model_res.error}")
        raise HTTPException(status_code=500, detail=f"Error cargando modelo: {model_res.error}")
    whisper_model = model_res.value

    # 4. Transcribir (en thread pool para no bloquear el event loop)
    def _do_transcribe():
        if model == PARAKEET:
            # Multilingue con deteccion implicita; no usa language/beam/vad.
            return whisper_model.transcribe(audio_data, language), None
        segments, info = whisper_model.transcribe(
            audio_data,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        # segments es generator, hay que consumirlo
        text_parts = []
        for seg in segments:
            text_parts.append(seg.text.strip())
        return " ".join(text_parts).strip(), info

    try:
        loop = asyncio.get_event_loop()
        with tracer.span("stt.transcribe.run", {"model": model}):
            text, info = await loop.run_in_executor(None, _do_transcribe)
    except Exception as e:
        logger.error(f"Error en transcripcion: {e}")
        err_counter.inc()
        raise HTTPException(status_code=500, detail=f"Error transcribiendo: {e}")

    elapsed = time.time() - t_start
    elapsed_ms = round(elapsed * 1000, 1)
    rtf = elapsed / max(duration_s, 0.01)  # real-time factor
    elapsed_hist.observe(elapsed_ms)
    rtf_hist.observe(rtf)
    lang_out = getattr(info, "language", None) or language or "auto"
    reg.counter("stt.transcribe.success", {"model": model, "language": lang_out}).inc()

    logger.info(
        "STT OK model=%s lang=%s text=%r duration=%.2fs elapsed=%.2fs rtf=%.2f",
        model, getattr(info, "language", None) or "auto",
        (text[:80] + ("..." if len(text) > 80 else "")),
        duration_s, elapsed, rtf,
    )

    return {
        "text": text,
        "language": lang_out,
        "language_probability": float(info.language_probability) if info else None,
        "duration_s": duration_s,
        "model": model,
        "elapsed_s": round(elapsed, 3),
        "rtf": round(rtf, 3),
    }


# === MAIN ===
_START_TIME = time.time()


def parse_args():
    p = argparse.ArgumentParser(description="STT Microservice (faster-whisper)")
    p.add_argument("--host", default=bind_host(), help="Host to bind")
    p.add_argument("--port", type=int, default=8000, help="Port to bind")
    p.add_argument("--model", default="tiny", choices=_AVAILABLE_MODELS, help="Default model")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"], help="Device")
    p.add_argument("--compute-type", default="int8", help="Compute type (int8, float16, float32)")
    p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    _DEFAULT_MODEL_NAME = args.model
    _DEFAULT_DEVICE = args.device
    _DEFAULT_COMPUTE_TYPE = args.compute_type

    print("=" * 60)
    print(f"  STT Microservice (faster-whisper)")
    print("=" * 60)
    print(f"  URL:      http://{args.host}:{args.port}")
    print(f"  Modelo:   {args.model}")
    print(f"  Device:   {args.device} ({args.compute_type})")
    print(f"  Health:   http://{args.host}:{args.port}/health")
    print(f"  Models:   http://{args.host}:{args.port}/models")
    print(f"  Docs:     http://{args.host}:{args.port}/docs")
    print("=" * 60)
    print()

    uvicorn.run(
        "stt_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
