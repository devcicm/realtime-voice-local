"""Motores intercambiables para RealtimeSession.

Cada motor es un objeto sencillo; los tests (o cualquier otro servidor)
pueden inyectar los suyos mientras respeten la misma firma:

    STT:  transcribe(audio_f32_16k: np.ndarray, language: str | None) -> str      (sincrono, en hilo)
    LLM:  stream(messages, **opts) -> AsyncIterator[str]                           (cancelable)
    TTS:  sample_rate: int ; synthesize(text, speed) -> Iterator[np.ndarray f32]   (sincrono, en hilo)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import AsyncIterator, Iterator, Optional

import numpy as np

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- STT
class ParakeetSTT:
    """NVIDIA Parakeet TDT 0.6B v3 (ONNX int8, via onnx-asr). Multilingue, 25 idiomas.

    Benchmark en CPU (i7-11700, 60 frases en espanol, ~3 s cada una):
      parakeet-tdt-0.6b-v3 int8 : WER 2.5 % limpio / 7.8 % con ruido, p50 261 ms
      whisper small beam 5      : WER 5.3 % / 8.6 %, p50 4.2 s
      whisper tiny beam 1       : WER 26 % / 42 %, p50 535 ms
    El encoder es de longitud variable (whisper siempre procesa 30 s), por eso
    escala con la duracion real de la frase. El idioma lo detecta solo.
    """

    MODEL = "nemo-parakeet-tdt-0.6b-v3"

    def __init__(self, model: str = MODEL, quantization: Optional[str] = "int8",
                 cpu_threads: int = 0) -> None:
        self.model_name = model
        self.quantization = quantization
        self.cpu_threads = cpu_threads
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    import onnx_asr
                    import onnxruntime
                    opts = onnxruntime.SessionOptions()
                    if self.cpu_threads:
                        opts.intra_op_num_threads = self.cpu_threads
                    opts.log_severity_level = 3
                    logger.info("realtime STT: cargando %s (%s)", self.model_name, self.quantization)
                    self._model = onnx_asr.load_model(self.model_name, quantization=self.quantization,
                                                      sess_options=opts)
        return self._model

    def warmup(self) -> None:
        self.transcribe(np.zeros(16000, dtype=np.float32), "es")

    def transcribe(self, audio: np.ndarray, language: Optional[str] = None) -> str:
        text = self.load().recognize(np.ascontiguousarray(audio, dtype=np.float32), sample_rate=16000)
        # Parakeet a veces pega la puntuacion a la palabra siguiente ("Hola,¿como").
        return _PUNCT_GLUE.sub(r"\1 ", (text or "").strip())


# Puntuacion seguida de letra o de ¿/¡ (no de digito, para no romper "3.5").
_PUNCT_GLUE = re.compile(r"([,.;:!?])(?=[¿¡]|[^\W\d_])")


def stt_from_env():
    """REALTIME_STT_ENGINE=parakeet (defecto) | whisper. Si onnx-asr no esta
    instalado, cae a whisper."""
    engine = os.getenv("REALTIME_STT_ENGINE", "parakeet").strip().lower()
    threads = int(os.getenv("REALTIME_STT_THREADS", "0"))
    if engine == "parakeet":
        try:
            import onnx_asr  # noqa: F401
            q = os.getenv("REALTIME_STT_QUANT", "int8").strip() or None
            return ParakeetSTT(quantization=None if q in (None, "fp32", "none") else q,
                               cpu_threads=threads)
        except ImportError:
            logger.warning("onnx-asr no instalado: usando whisper (pip install onnx-asr)")
    return WhisperSTT.from_env()


class WhisperSTT:
    """faster-whisper en proceso, modelo caliente (alternativa a Parakeet).

    En CPU, `base` + beam 5 es el mejor equilibrio (WER ~9 %, ~1.2 s);
    `tiny` confunde 1 de cada 4 palabras en espanol.
    """

    def __init__(self, model: str = "base", device: str = "cpu",
                 compute_type: str = "int8", beam_size: int = 5,
                 cpu_threads: int = 0) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.cpu_threads = cpu_threads
        self._model = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "WhisperSTT":
        return cls(
            model=os.getenv("REALTIME_STT_MODEL", "base"),
            device=os.getenv("REALTIME_STT_DEVICE", "cpu"),
            compute_type=os.getenv("REALTIME_STT_COMPUTE", "int8"),
            beam_size=int(os.getenv("REALTIME_STT_BEAM", "5")),
            cpu_threads=int(os.getenv("REALTIME_STT_THREADS", "0")),
        )

    def load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from faster_whisper import WhisperModel
                    logger.info("realtime STT: cargando whisper %s (%s/%s)",
                                self.model_name, self.device, self.compute_type)
                    self._model = WhisperModel(self.model_name, device=self.device,
                                               compute_type=self.compute_type,
                                               cpu_threads=self.cpu_threads)
        return self._model

    def warmup(self) -> None:
        self.transcribe(np.zeros(16000, dtype=np.float32), "es")

    def transcribe(self, audio: np.ndarray, language: Optional[str] = None) -> str:
        model = self.load()
        segments, _info = model.transcribe(
            audio,
            language=language or None,
            beam_size=self.beam_size,
            best_of=1,
            temperature=0.0,
            without_timestamps=True,
            condition_on_previous_text=False,
            vad_filter=False,  # ya segmentamos con Silero
        )
        return " ".join(s.text.strip() for s in segments).strip()


# --------------------------------------------------------------------------- LLM
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class OpenAICompatLLM:
    """Streaming SSE contra LM Studio / Ollama / llama.cpp (API OpenAI).

    Cancelar la tarea que itera `stream()` cierra la conexion httpx, y
    LM Studio deja de generar al detectar la desconexion.
    """

    def __init__(self, base_url: str, model: str, timeout_s: float = 60.0,
                 reasoning_effort: Optional[str] = None) -> None:
        # localhost -> ::1 primero en Windows; si el servidor solo escucha en
        # IPv4, cada conexion nueva (p.ej. tras un barge-in) pierde ~2 s.
        base = re.sub(r"//localhost(?=[:/]|$)", "//127.0.0.1", base_url.rstrip("/"))
        if not base.endswith("/v1"):
            base += "/v1"
        self.url = base + "/chat/completions"
        self.model = model
        self.timeout_s = timeout_s
        # Voz: sin "thinking" (gemma-4/qwen3 razonan antes de responder y el
        # primer token visible tarda decenas de segundos). "" = no enviar.
        if reasoning_effort is None:
            reasoning_effort = os.getenv("REALTIME_LLM_REASONING", "none")
        self.reasoning_effort = reasoning_effort
        self._client = None

    def _http(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s,
                limits=httpx.Limits(max_keepalive_connections=4),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def stream(self, messages: list[dict], model: Optional[str] = None,
                     temperature: float = 0.7, max_tokens: int = 512,
                     run_id: Optional[str] = None) -> AsyncIterator[str]:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        headers = {"X-Run-Id": run_id} if run_id else {}
        in_think = False
        async with self._http().stream("POST", self.url, json=payload, headers=headers) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")[:300]
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {body}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError):
                    continue
                text = delta.get("content") or ""
                if not text:
                    continue
                # Modelos con razonamiento inline: no leer en voz alta el <think>.
                if "<think>" in text:
                    in_think = True
                    text = text.split("<think>", 1)[0]
                if in_think:
                    if "</think>" not in text:
                        continue
                    in_think = False
                    text = text.split("</think>", 1)[1]
                if text:
                    yield text


# --------------------------------------------------------------------------- TTS
class BackendTTS:
    """Adapta un `TTSBackend` (server/shared/backends) al motor realtime."""

    def __init__(self, backend, chunk_size: int = 2400) -> None:
        self.backend = backend
        self.chunk_size = chunk_size

    @property
    def sample_rate(self) -> int:
        return int(self.backend.get_sample_rate())

    def synthesize(self, text: str, speed: float = 1.0) -> Iterator[np.ndarray]:
        for chunk, _last in self.backend.generate_streaming(text, speed=speed,
                                                            chunk_size=self.chunk_size):
            if chunk is not None and len(chunk):
                yield np.asarray(chunk, dtype=np.float32)


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def pcm16_to_float(data: bytes) -> np.ndarray:
    n = len(data) // 2
    return np.frombuffer(data[: n * 2], dtype="<i2").astype(np.float32) / 32768.0
