"""RealtimeSession: orquestador VAD -> STT -> LLM -> TTS con barge-in.

Una instancia por conexion. No depende de FastAPI: recibe dos corutinas
`send_json(dict)` y `send_bytes(bytes)` y los motores (ver engines.py).

Flujo de un turno por voz
  1. `feed_audio()` pasa PCM16 por el VAD (frames de 32 ms, <1 ms c/u).
  2. Fin de voz (silence_ms) -> se crea una respuesta:
     STT (hilo) -> LLM en streaming -> ClauseChunker -> TTS (hilo) -> PCM16.
     LLM y TTS corren en paralelo: el TTS sintetiza la clausula 1 mientras
     el LLM sigue generando la 2.
  3. Inicio de voz mientras hay respuesta:
     - si ya se envio audio: BARGE-IN. Se cancela LLM+TTS al instante, el
       cliente vacia su buffer y responde `response.truncate{played_ms}`;
       al contexto solo pasa lo que el usuario oyo (marcado interrumpido).
     - si aun no hubo audio (el usuario solo hizo una pausa): se cancela
       y se REPROCESA: el audio previo se une al nuevo y se transcribe
       todo junto, como un unico turno.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from collections import deque
from dataclasses import dataclass, field, fields
from typing import Any, Awaitable, Callable, Optional

import numpy as np

from server.shared.conversation.log import ConversationLog
from server.shared.conversation.turn_controller import TurnController

from .chunker import ClauseChunker, heard_text
from .engines import float_to_pcm16, pcm16_to_float
from .protocol import INPUT_SAMPLE_RATE, PROTOCOL_VERSION, pack_audio
from .vad import FRAME_MS, StreamingVAD, VADConfig

logger = logging.getLogger(__name__)

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]

# Whisper es pesado: un unico hilo compartido entre sesiones evita que
# dos transcripciones compitan por la CPU y se ralenticen ambas.
_STT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rt-stt")


# Reglas de salida para voz: respuestas cortas (primer audio antes y menos
# tokens a ~12 tok/s en CPU) y nada que Piper lea mal.
VOICE_STYLE = (
    " Tu respuesta se lee en voz alta: responde en una a tres frases cortas,"
    " sin emojis, listas, markdown ni simbolos; escribe los numeros como se dicen."
)


@dataclass
class SessionConfig:
    instructions: str = "Eres un asistente de voz amigable. Responde en español, breve y natural."
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 256
    language: Optional[str] = "es"
    speed: float = 1.0
    barge_in: bool = True
    min_speech_ms: float = 200.0     # voz util minima para considerar un turno
    vad: VADConfig = field(default_factory=VADConfig)

    def update(self, d: dict) -> None:
        for f in fields(self):
            if f.name not in (d or {}):
                continue
            v = d[f.name]
            if f.name == "vad":
                self.vad.update(v)
            elif f.name == "barge_in":
                self.barge_in = bool(v)
            elif f.name in ("temperature", "speed", "min_speech_ms"):
                setattr(self, f.name, float(v))
            elif f.name == "max_tokens":
                self.max_tokens = int(v)
            else:
                setattr(self, f.name, v)

    def to_dict(self) -> dict:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "vad"}
        d["vad"] = self.vad.to_dict()
        return d


@dataclass
class _Response:
    id: int
    source: str                         # 'audio' | 'text'
    input_audio: Optional[np.ndarray] = None
    user_text: str = ""
    text: str = ""
    segments: list = field(default_factory=list)   # [[text, start_ms, dur_ms], ...]
    audio_ms_sent: float = 0.0
    first_audio_at: Optional[float] = None
    played_ms: Optional[float] = None   # ultimo playback.position del cliente
    task: Optional[asyncio.Task] = None
    run: Any = None
    done: bool = False
    marks: dict = field(default_factory=dict)
    stt_future: Optional[concurrent.futures.Future] = None  # STT especulativo ya lanzado

    def estimate_played_ms(self) -> float:
        if self.played_ms is not None:
            return min(self.played_ms, self.audio_ms_sent)
        if self.first_audio_at is None:
            return 0.0
        return min(self.audio_ms_sent, (time.perf_counter() - self.first_audio_at) * 1000.0)


def _ms(t0: Optional[float], t1: Optional[float]) -> Optional[float]:
    if t0 is None or t1 is None:
        return None
    return round((t1 - t0) * 1000.0, 1)


class RealtimeSession:
    def __init__(self, send_json: SendJson, send_bytes: SendBytes, *, stt, llm, tts,
                 config: Optional[SessionConfig] = None,
                 log: Optional[ConversationLog] = None,
                 vad: Optional[StreamingVAD] = None,
                 stt_executor: Optional[concurrent.futures.Executor] = None,
                 session_id: str = "rt") -> None:
        self._send_json_raw = send_json
        self._send_bytes_raw = send_bytes
        self._send_lock = asyncio.Lock()
        self.stt, self.llm, self.tts = stt, llm, tts
        self.config = config or SessionConfig()
        self.log = log or ConversationLog(window_size=24)
        self.vad = vad or StreamingVAD(self.config.vad)
        self.vad.config = self.config.vad
        self.turns = TurnController()  # sin log: el truncado lo hace la sesion
        self.session_id = session_id
        self._stt_exec = stt_executor or _STT_EXECUTOR
        self._tts_exec = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="rt-tts")
        self._next_id = 1
        self._active: Optional[_Response] = None
        self._pending_truncate: Optional[_Response] = None
        self._carry_audio: Optional[np.ndarray] = None
        self._carry_text: str = ""
        self._speech_started_at: Optional[float] = None
        self._spec: Optional[tuple] = None   # (future, carry usado) del STT especulativo
        self.frame_cost_ms: deque = deque(maxlen=2000)
        self._closed = False

    # ------------------------------------------------------------------ io
    async def _send(self, obj: dict) -> None:
        if self._closed:
            return
        async with self._send_lock:
            await self._send_json_raw(obj)

    async def _send_audio(self, frame: bytes) -> None:
        if self._closed:
            return
        async with self._send_lock:
            await self._send_bytes_raw(frame)

    async def start(self) -> None:
        await self._send({
            "type": "session.created",
            "protocol": PROTOCOL_VERSION,
            "session": self.config.to_dict(),
            "audio": {
                "format": "pcm16",
                "input_sample_rate": INPUT_SAMPLE_RATE,
                "output_sample_rate": self.tts.sample_rate,
            },
        })

    async def close(self) -> None:
        r = self._active
        if r and r.task and not r.task.done():
            r.task.cancel()
            await asyncio.gather(r.task, return_exceptions=True)
        self._closed = True
        self._tts_exec.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- input
    async def feed_audio(self, data: bytes) -> None:
        t0 = time.perf_counter()
        frames_before = self.vad.frames_processed
        events = self.vad.process(pcm16_to_float(data))
        n = self.vad.frames_processed - frames_before
        if n:
            self.frame_cost_ms.append((time.perf_counter() - t0) * 1000.0 / n)
        for kind, audio in events:
            if kind == "start":
                await self._on_speech_start()
            elif kind == "pause":
                self._on_pause(audio)
            else:
                await self._on_speech_end(audio)

    async def handle_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "session.update":
            self.config.update(ev.get("session") or {})
            await self._send({"type": "session.updated", "session": self.config.to_dict()})
        elif t == "input_audio.commit":
            audio = self.vad.force_end()
            if audio is not None:
                await self._send({"type": "input_audio.speech_stopped", "audio_ms": self.vad.audio_ms})
                await self._on_speech_end(audio, forced=True)
        elif t == "input_audio.clear":
            self.vad.clear_utterance()
        elif t == "conversation.item.create":
            text = (ev.get("text") or "").strip()
            if text:
                await self._preempt_active(reason="new_input")
                await self._start_response("text", user_text=text)
        elif t == "response.cancel":
            await self._preempt_active(reason="client_cancel", carry=False)
        elif t == "playback.position":
            r = self._find(ev.get("response_id"))
            if r is not None and ev.get("played_ms") is not None:
                r.played_ms = float(ev["played_ms"])
        elif t == "response.truncate":
            r = self._pending_truncate
            if r is not None and ev.get("response_id") in (None, r.id):
                await self._finalize_pending(float(ev.get("played_ms") or 0.0))
            else:
                r = self._find(ev.get("response_id"))
                if r is not None and ev.get("played_ms") is not None:
                    r.played_ms = float(ev["played_ms"])
        elif t == "conversation.clear":
            await self._preempt_active(reason="clear", carry=False)
            self._pending_truncate = None
            self._carry_audio, self._carry_text = None, ""
            self.log.clear()
        elif t == "ping":
            await self._send({"type": "pong", "t": ev.get("t")})
        else:
            await self._send({"type": "error", "message": f"evento desconocido: {t!r}"})

    def _find(self, rid) -> Optional[_Response]:
        for r in (self._active, self._pending_truncate):
            if r is not None and (rid is None or r.id == rid):
                return r
        return None

    # ----------------------------------------------------- STT especulativo
    def _on_pause(self, audio: np.ndarray) -> None:
        """Primeros ms de silencio: transcribir ya, mientras se confirma el fin
        de turno. Si la voz vuelve, el VAD emitira otro "pause" mas adelante."""
        self._drop_spec()
        cfg = self.config
        speech_ms = len(audio) * 1000.0 / INPUT_SAMPLE_RATE - cfg.vad.prefix_ms - cfg.vad.early_ms
        carry = self._carry_audio
        if speech_ms < cfg.min_speech_ms and carry is None:
            return
        if carry is not None:
            audio = np.concatenate([carry, audio])
        fut = self._stt_exec.submit(self.stt.transcribe, audio, cfg.language)
        self._spec = (fut, carry)

    def _drop_spec(self) -> None:
        if self._spec is not None:
            self._spec[0].cancel()  # si aun no empezo, libera el hilo de STT
            self._spec = None

    async def _on_speech_start(self) -> None:
        self._drop_spec()
        self._speech_started_at = time.perf_counter()
        r = self._active
        await self._send({
            "type": "input_audio.speech_started",
            "audio_ms": round(self.vad.audio_ms, 1),
            "response_id": r.id if r and not r.done else None,
        })
        if r and not r.done and self.config.barge_in:
            await self._preempt_active(reason="barge-in")

    async def _on_speech_end(self, audio: np.ndarray, forced: bool = False) -> None:
        if not forced:
            await self._send({"type": "input_audio.speech_stopped", "audio_ms": round(self.vad.audio_ms, 1)})
        cfg = self.config
        spec, self._spec = self._spec, None
        speech_ms = len(audio) * 1000.0 / INPUT_SAMPLE_RATE
        if not forced:
            speech_ms -= cfg.vad.prefix_ms + cfg.vad.silence_ms
        if speech_ms < cfg.min_speech_ms and self._carry_audio is None:
            if spec:
                spec[0].cancel()
            return  # ruido / tos: no es un turno
        # Solo llega aqui con respuesta activa si barge_in esta desactivado.
        await self._preempt_active(reason="new_input")
        if spec is not None and (forced or spec[1] is not self._carry_audio):
            spec[0].cancel()  # el audio a transcribir ya no es el mismo
            spec = None
        if self._carry_audio is not None:
            audio = np.concatenate([self._carry_audio, audio])
            self._carry_audio = None
        await self._start_response("audio", input_audio=audio,
                                   stt_future=spec[0] if spec else None)

    # --------------------------------------------------------- cancel
    async def _preempt_active(self, reason: str, carry: bool = True) -> None:
        """Corta la respuesta activa. Con audio ya enviado = barge-in
        (truncado); sin audio = el usuario siguio hablando (reproceso)."""
        r = self._active
        if r is None or r.done:
            return
        t0 = self._speech_started_at if reason == "barge-in" else time.perf_counter()
        if r.task and not r.task.done():
            r.task.cancel()
            await asyncio.gather(r.task, return_exceptions=True)
        r.done = True
        self._active = None
        await self.turns.interrupt(reason)
        if r.audio_ms_sent > 0:
            r.marks["played_estimate_ms"] = r.estimate_played_ms()
            if self._pending_truncate is not None:
                await self._finalize_pending(None)
            self._pending_truncate = r
        elif carry:
            if r.source == "audio" and r.input_audio is not None:
                self._carry_audio = r.input_audio
            elif r.user_text:
                self._carry_text = (self._carry_text + " " + r.user_text).strip()
            reason = "user_continued"  # se reprocesara junto con lo nuevo
        await self._send({
            "type": "response.cancelled",
            "response_id": r.id,
            "reason": reason,
            "audio_ms": round(r.audio_ms_sent, 1),
            "interrupt_ms": _ms(t0, time.perf_counter()),
        })

    async def _finalize_pending(self, played_ms: Optional[float]) -> None:
        r = self._pending_truncate
        if r is None:
            return
        self._pending_truncate = None
        if played_ms is None:
            played_ms = r.marks.get("played_estimate_ms", r.estimate_played_ms())
        played_ms = max(0.0, min(played_ms, r.audio_ms_sent))
        heard = heard_text([tuple(s) for s in r.segments], played_ms)
        self._commit(r, heard + "…" if heard else "", interrupted=True)
        await self._send({
            "type": "conversation.truncated",
            "response_id": r.id,
            "heard_text": heard,
            "played_ms": round(played_ms, 1),
        })

    def _commit(self, r: _Response, assistant_text: str, interrupted: bool) -> None:
        if r.user_text:
            self.log.add_user_turn(r.user_text, source="stt" if r.source == "audio" else "text")
        if assistant_text:
            self.log.add_assistant_turn(assistant_text)
            if interrupted:
                self.log.mark_interrupted("assistant", "barge-in")

    def _build_messages(self, user_text: str) -> list[dict]:
        msgs = self.log.to_messages(system_prompt=self.config.instructions or None)
        msgs.append({"role": "user", "content": user_text})
        out: list[dict] = []
        for m in msgs:
            if m["role"] != "system" and not any(x["role"] != "system" for x in out) and m["role"] != "user":
                continue  # la ventana empezo en un turno del asistente
            if out and out[-1]["role"] == m["role"] and m["role"] != "system":
                out[-1] = {"role": m["role"], "content": out[-1]["content"] + " " + m["content"]}
            else:
                out.append(dict(m))
        return out

    # -------------------------------------------------------- response
    async def _start_response(self, source: str, input_audio=None, user_text: str = "",
                              stt_future=None) -> None:
        r = _Response(id=self._next_id, source=source, input_audio=input_audio, user_text=user_text,
                      stt_future=stt_future)
        self._next_id += 1
        r.marks["t_end"] = time.perf_counter()
        r.run = await self.turns.new_run(session_id=self.session_id, turn_id=str(r.id))
        self._active = r
        r.task = asyncio.create_task(self._run_response(r))

    async def _run_response(self, r: _Response) -> None:
        loop = asyncio.get_running_loop()
        cfg = self.config
        tts_task: Optional[asyncio.Task] = None
        m = r.marks
        try:
            await self._send({"type": "response.created", "response_id": r.id})
            if r.source == "audio":
                text = None
                if r.stt_future is not None and not r.stt_future.cancelled():
                    try:
                        text = await asyncio.wrap_future(r.stt_future)
                        m["stt_speculative"] = True
                    except concurrent.futures.CancelledError:
                        text = None
                if text is None:
                    text = await loop.run_in_executor(self._stt_exec, self.stt.transcribe,
                                                      r.input_audio, cfg.language)
                m["t_stt"] = time.perf_counter()
                text = text.strip()
                if self._carry_text:
                    text = (self._carry_text + " " + text).strip()
                    self._carry_text = ""
                if not text:
                    r.done = True
                    self._active = None
                    await self.turns.complete()
                    await self._send({"type": "response.cancelled", "response_id": r.id,
                                      "reason": "empty_transcript", "audio_ms": 0})
                    return
                r.user_text = text
                await self._send({"type": "transcript.final", "response_id": r.id, "text": text})
            elif self._carry_text:
                r.user_text = (self._carry_text + " " + r.user_text).strip()
                self._carry_text = ""

            # El cliente ya tuvo tiempo de mandar response.truncate (llega
            # mientras corre el STT); si no, se usa la estimacion del servidor.
            await self._finalize_pending(None)
            messages = self._build_messages(r.user_text)

            queue: asyncio.Queue = asyncio.Queue()
            tts_task = asyncio.create_task(self._tts_loop(r, queue))
            chunker = ClauseChunker()
            m["t_llm_req"] = time.perf_counter()
            async for delta in self.llm.stream(messages, model=cfg.model,
                                               temperature=cfg.temperature,
                                               max_tokens=cfg.max_tokens,
                                               run_id=r.run.run_id if r.run else None):
                if "t_llm_first" not in m:
                    m["t_llm_first"] = time.perf_counter()
                r.text += delta
                await self._send({"type": "response.text.delta", "response_id": r.id, "delta": delta})
                for piece in chunker.push(delta):
                    if "t_first_piece" not in m:
                        m["t_first_piece"] = time.perf_counter()
                    queue.put_nowait(piece)
            for piece in chunker.flush():
                m.setdefault("t_first_piece", time.perf_counter())
                queue.put_nowait(piece)
            m["t_llm_done"] = time.perf_counter()
            queue.put_nowait(None)
            await tts_task
            m["t_done"] = time.perf_counter()

            r.done = True
            if self._active is r:
                self._active = None
            self._commit(r, r.text.strip(), interrupted=False)
            await self.turns.complete()
            await self._send({"type": "response.done", "response_id": r.id,
                              "text": r.text.strip(), "audio_ms": round(r.audio_ms_sent, 1)})
            await self._send(self._turn_metrics(r))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # motor caido, LLM no disponible, etc.
            logger.exception("realtime response %s failed", r.id)
            r.done = True
            if self._active is r:
                self._active = None
            await self.turns.fail(e)
            await self._send({"type": "error", "response_id": r.id, "message": str(e)})
            await self._send({"type": "response.cancelled", "response_id": r.id,
                              "reason": "error", "audio_ms": round(r.audio_ms_sent, 1)})
        finally:
            if tts_task is not None and not tts_task.done():
                tts_task.cancel()
                await asyncio.gather(tts_task, return_exceptions=True)

    async def _tts_loop(self, r: _Response, queue: asyncio.Queue) -> None:
        loop = asyncio.get_running_loop()
        sr = self.tts.sample_rate
        seq = 0
        index = 0
        while True:
            piece = await queue.get()
            if piece is None:
                return
            seg = [piece, r.audio_ms_sent, 0.0]
            r.segments.append(seg)
            await self._send({"type": "response.segment", "response_id": r.id, "index": index,
                              "text": piece, "start_ms": round(seg[1], 1)})
            index += 1
            it = iter(self.tts.synthesize(piece, self.config.speed))
            t_synth = time.perf_counter()
            while True:
                chunk = await loop.run_in_executor(self._tts_exec, next, it, None)
                if chunk is None:
                    break
                now = time.perf_counter()
                if r.first_audio_at is None:
                    r.first_audio_at = now
                    r.marks["t_first_audio"] = now
                    r.marks["tts_first_chunk_ms"] = _ms(t_synth, now)
                await self._send_audio(pack_audio(r.id, seq, float_to_pcm16(chunk)))
                seq += 1
                dur = len(chunk) * 1000.0 / sr
                r.audio_ms_sent += dur
                seg[2] += dur

    # ---------------------------------------------------------- metrics
    def gateway_stats(self) -> dict:
        costs = sorted(self.frame_cost_ms)
        if not costs:
            return {}
        pick = lambda q: round(costs[min(len(costs) - 1, int(q * len(costs)))], 3)
        return {"frame_ms": round(FRAME_MS, 1), "p50_ms": pick(0.5), "p99_ms": pick(0.99),
                "max_ms": round(costs[-1], 3), "frames": len(costs)}

    def _turn_metrics(self, r: _Response) -> dict:
        m = r.marks
        t_end = m.get("t_end")
        out = {
            "type": "metrics.turn",
            "response_id": r.id,
            "source": r.source,
            "stt_ms": _ms(t_end, m.get("t_stt")),
            "stt_speculative": bool(m.get("stt_speculative")),
            "llm_first_token_ms": _ms(m.get("t_llm_req"), m.get("t_llm_first")),
            "first_clause_ms": _ms(t_end, m.get("t_first_piece")),
            "tts_first_chunk_ms": m.get("tts_first_chunk_ms"),
            "first_audio_ms": _ms(t_end, m.get("t_first_audio")),
            "llm_total_ms": _ms(m.get("t_llm_req"), m.get("t_llm_done")),
            "total_ms": _ms(t_end, m.get("t_done")),
            "audio_ms": round(r.audio_ms_sent, 1),
            "gateway": self.gateway_stats(),
        }
        try:
            from server.shared.observability import get_registry
            reg = get_registry()
            for k in ("stt_ms", "llm_first_token_ms", "first_audio_ms"):
                if out[k] is not None:
                    reg.histogram(f"realtime_{k}").observe(out[k])
        except Exception:
            pass
        return out
