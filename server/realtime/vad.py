"""StreamingVAD: Silero v6 (ONNX) con estado incremental.

Reutiliza el modelo `silero_vad_v6.onnx` que ya trae faster-whisper.
Procesa frames de 512 muestras (32 ms a 16 kHz); cada inferencia cuesta
~0.1-0.3 ms en CPU, asi que el overhead por frame queda muy por debajo
de 2 ms.

Maquina de estados con histeresis:
  silencio --(prob >= threshold durante min_speech_ms)--> voz      => "start"
  voz      --(prob <  neg_threshold durante silence_ms)--> silencio => "end"

Con `early_ms` (< silence_ms) se emite antes un "pause" con el audio hasta
ese punto: la sesion arranca el STT en especulativo mientras espera a
confirmar el fin de turno, y si la voz no vuelve reutiliza el resultado.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, fields
from typing import Callable, Optional

import numpy as np

FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64
SAMPLE_RATE = 16000
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE  # 32 ms

_session = None
_session_lock = threading.Lock()


def _silero_session():
    """InferenceSession compartida (thread-safe); el estado h/c va por instancia."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                import onnxruntime
                from faster_whisper.utils import get_assets_path
                opts = onnxruntime.SessionOptions()
                opts.inter_op_num_threads = 1
                opts.intra_op_num_threads = 1
                opts.enable_cpu_mem_arena = False
                opts.log_severity_level = 4
                _session = onnxruntime.InferenceSession(
                    os.path.join(get_assets_path(), "silero_vad_v6.onnx"),
                    providers=["CPUExecutionProvider"],
                    sess_options=opts,
                )
    return _session


class SileroFrameModel:
    """Silero con estado entre llamadas (una instancia por stream)."""

    def __init__(self) -> None:
        self._sess = _silero_session()
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._context, frame])[None, :]
        prob, self._h, self._c = self._sess.run(None, {"input": x, "h": self._h, "c": self._c})
        self._context = frame[-CONTEXT_SAMPLES:]
        return float(np.asarray(prob).reshape(-1)[0])


@dataclass
class VADConfig:
    threshold: float = 0.5
    neg_threshold: Optional[float] = None  # None = threshold - 0.15
    min_speech_ms: float = 96.0
    silence_ms: float = 350.0
    prefix_ms: float = 300.0
    early_ms: float = 128.0      # 0 = sin STT especulativo
    max_utterance_ms: float = 30000.0

    def update(self, d: dict) -> None:
        names = {f.name for f in fields(self)}
        for k, v in (d or {}).items():
            if k in names and v is not None:
                setattr(self, k, float(v))

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class StreamingVAD:
    """Alimenta muestras float32 a 16 kHz; devuelve eventos de frontera.

    `process()` devuelve una lista de tuplas (evento, audio):
      ("start", None)      al confirmar voz
      ("pause", np.ndarray) tras early_ms de silencio (puede no acabar en "end")
      ("end", np.ndarray)  al terminar; audio = prefijo + voz + cola de silencio
    """

    def __init__(self, config: Optional[VADConfig] = None,
                 model: Optional[Callable[[np.ndarray], float]] = None) -> None:
        self.config = config or VADConfig()
        self._model = model if model is not None else SileroFrameModel()
        self._pending = np.zeros(0, dtype=np.float32)
        self._prefix: list[np.ndarray] = []
        self._utterance: list[np.ndarray] = []
        self.speaking = False
        self._speech_run = 0
        self._silence_run = 0
        self.frames_processed = 0
        self.last_prob = 0.0

    @property
    def audio_ms(self) -> float:
        """Posicion del stream de entrada (ms de audio procesado)."""
        return self.frames_processed * FRAME_MS

    def clear_utterance(self) -> None:
        self._utterance = []
        self.speaking = False
        self._speech_run = 0
        self._silence_run = 0

    def force_end(self) -> Optional[np.ndarray]:
        """Cierra el turno ya (push-to-talk / input_audio.commit)."""
        if not self._utterance:
            self.clear_utterance()
            return None
        audio = np.concatenate(self._utterance)
        self.clear_utterance()
        return audio

    def process(self, samples: np.ndarray) -> list[tuple[str, Optional[np.ndarray]]]:
        cfg = self.config
        neg = cfg.neg_threshold if cfg.neg_threshold is not None else max(0.05, cfg.threshold - 0.15)
        start_frames = max(1, round(cfg.min_speech_ms / FRAME_MS))
        end_frames = max(1, round(cfg.silence_ms / FRAME_MS))
        prefix_frames = max(start_frames, round(cfg.prefix_ms / FRAME_MS))
        max_frames = max(1, round(cfg.max_utterance_ms / FRAME_MS))
        early_frames = round(cfg.early_ms / FRAME_MS) if cfg.early_ms > 0 else 0
        if early_frames >= end_frames:
            early_frames = 0

        buf = samples if self._pending.size == 0 else np.concatenate([self._pending, samples])
        n = (buf.size // FRAME_SAMPLES) * FRAME_SAMPLES
        self._pending = buf[n:].copy()
        events: list[tuple[str, Optional[np.ndarray]]] = []

        for off in range(0, n, FRAME_SAMPLES):
            frame = buf[off:off + FRAME_SAMPLES]
            prob = self._model(frame)
            self.last_prob = prob
            self.frames_processed += 1

            if not self.speaking:
                self._prefix.append(frame)
                if len(self._prefix) > prefix_frames:
                    self._prefix.pop(0)
                self._speech_run = self._speech_run + 1 if prob >= cfg.threshold else 0
                if self._speech_run >= start_frames:
                    self.speaking = True
                    self._silence_run = 0
                    self._utterance = self._prefix
                    self._prefix = []
                    events.append(("start", None))
            else:
                self._utterance.append(frame)
                self._silence_run = self._silence_run + 1 if prob < neg else 0
                if early_frames and self._silence_run == early_frames:
                    events.append(("pause", np.concatenate(self._utterance)))
                if self._silence_run >= end_frames or len(self._utterance) >= max_frames:
                    audio = np.concatenate(self._utterance)
                    self.clear_utterance()
                    events.append(("end", audio))
        return events
