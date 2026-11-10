"""Tests del modulo server/realtime (sin servicios externos)."""
import asyncio
import time

import numpy as np
import pytest

from server.realtime.chunker import ClauseChunker, heard_text
from server.realtime.protocol import pack_audio, unpack_audio
from server.realtime.session import RealtimeSession, SessionConfig
from server.realtime.vad import FRAME_SAMPLES, StreamingVAD, VADConfig

SR = 16000


# ----------------------------------------------------------------- helpers
def pcm(seconds: float, amp: float) -> bytes:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return (x * 32767).astype("<i2").tobytes()


def energy_model(frame: np.ndarray) -> float:
    return 1.0 if float(np.sqrt(np.mean(frame ** 2))) > 0.05 else 0.0


class FakeSTT:
    def __init__(self, text="hola", delay=0.0):
        self.text, self.delay, self.calls = text, delay, []

    def transcribe(self, audio, language=None):
        self.calls.append(len(audio))
        time.sleep(self.delay)
        return self.text


class FakeLLM:
    def __init__(self, deltas, delay=0.0):
        self.deltas, self.delay = deltas, delay
        self.requests, self.cancelled = [], 0

    async def stream(self, messages, **kw):
        self.requests.append(messages)
        try:
            for d in self.deltas:
                if self.delay:
                    await asyncio.sleep(self.delay)
                yield d
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


class FakeTTS:
    sample_rate = 16000

    def __init__(self, chunk_ms=100, chunks=2):
        self.chunk_ms, self.chunks, self.texts = chunk_ms, chunks, []

    def synthesize(self, text, speed=1.0):
        self.texts.append(text)
        for _ in range(self.chunks):
            yield np.zeros(int(self.sample_rate * self.chunk_ms / 1000), dtype=np.float32)


class Wire:
    def __init__(self):
        self.events, self.audio = [], []

    async def send_json(self, obj):
        self.events.append(obj)

    async def send_bytes(self, b):
        self.audio.append(b)

    def types(self):
        return [e["type"] for e in self.events]

    def of(self, t):
        return [e for e in self.events if e["type"] == t]


def make_session(stt=None, llm=None, tts=None, **cfg):
    wire = Wire()
    config = SessionConfig(instructions="sys", **cfg)
    config.vad = VADConfig(silence_ms=160, min_speech_ms=64, prefix_ms=96)
    s = RealtimeSession(wire.send_json, wire.send_bytes,
                        stt=stt or FakeSTT(), llm=llm or FakeLLM(["Hola, ", "¿qué tal? ", "Bien."]),
                        tts=tts or FakeTTS(), config=config,
                        vad=StreamingVAD(config.vad, model=energy_model))
    return s, wire


async def speak(s, seconds=0.5):
    await s.feed_audio(pcm(seconds, 0.5))


async def silence(s, seconds=0.3):
    await s.feed_audio(pcm(seconds, 0.0))


async def wait_for(cond, timeout=3.0):
    t0 = time.perf_counter()
    while not cond():
        if time.perf_counter() - t0 > timeout:
            raise AssertionError("timeout esperando condicion")
        await asyncio.sleep(0.005)


# ---------------------------------------------------------------- protocol
def test_pack_unpack_audio_roundtrip():
    frame = pack_audio(7, 3, b"\x01\x02\x03\x04")
    assert unpack_audio(frame) == (7, 3, b"\x01\x02\x03\x04")
    with pytest.raises(ValueError):
        unpack_audio(b"\x00")


# ----------------------------------------------------------------- chunker
def feed(chunker, text):
    out = []
    for ch in text:
        out += chunker.push(ch)
    return out + chunker.flush()


def test_chunker_first_clause_is_early_and_decimals_safe():
    pieces = feed(ClauseChunker(), "Claro, te cuento: el valor es 3.5 metros. Luego seguimos.")
    assert pieces[0] == "Claro, te cuento:"
    assert "3.5 metros." in pieces[1]
    assert pieces[-1] == "Luego seguimos."


def test_chunker_strips_emojis_for_tts():
    assert feed(ClauseChunker(), "¡Hola! 😊 Buenos Aires 🇦🇷.") == ["¡Hola!", "Buenos Aires."]


def test_chunker_splits_long_run_without_punctuation():
    text = " ".join(f"w{i}" for i in range(60))
    pieces = feed(ClauseChunker(max_words=20), text)
    assert len(pieces) >= 3
    assert " ".join(pieces).split() == text.split()


def test_heard_text_cuts_inside_segment():
    segs = [("Hola que tal", 0, 1000), ("muy bien gracias", 1000, 1000)]
    assert heard_text(segs, 0) == ""
    assert heard_text(segs, 1000) == "Hola que tal"
    assert heard_text(segs, 2500) == "Hola que tal muy bien gracias"
    mid = heard_text(segs, 1500)
    assert mid.startswith("Hola que tal muy") and "gracias" not in mid


# --------------------------------------------------------------------- VAD
def test_vad_start_end_with_prefix():
    vad = StreamingVAD(VADConfig(silence_ms=160, min_speech_ms=64, prefix_ms=96), model=energy_model)
    x = lambda b: np.frombuffer(b, dtype="<i2").astype(np.float32) / 32768
    assert vad.process(x(pcm(0.3, 0.0))) == []
    ev = vad.process(x(pcm(0.5, 0.5)))
    assert [e for e, _ in ev] == ["start"]
    ev = vad.process(x(pcm(0.3, 0.0)))
    assert [e for e, _ in ev] == ["pause", "end"]  # pause = early_ms (STT especulativo)
    pause_audio, audio = ev[0][1], ev[1][1]
    assert audio.size % FRAME_SAMPLES == 0
    assert audio.size >= int(0.5 * SR)  # voz + prefijo + cola
    assert pause_audio.size < audio.size
    vad2 = StreamingVAD(VADConfig(silence_ms=160, min_speech_ms=64, prefix_ms=96, early_ms=0),
                        model=energy_model)
    vad2.process(x(pcm(0.5, 0.5)))
    assert [e for e, _ in vad2.process(x(pcm(0.3, 0.0)))] == ["end"]


@pytest.mark.asyncio
async def test_speculative_stt_is_reused_at_end_of_turn():
    stt = FakeSTT("hola")
    s, wire = make_session(stt=stt)
    await s.start()
    await speak(s, 0.5)
    await silence(s, 0.3)
    await wait_for(lambda: wire.of("metrics.turn"))
    assert len(stt.calls) == 1               # no se transcribe dos veces
    assert wire.of("metrics.turn")[0]["stt_speculative"] is True
    assert wire.of("transcript.final")[0]["text"] == "hola"


@pytest.mark.asyncio
async def test_speculative_stt_discarded_when_speech_resumes():
    stt = FakeSTT("hola")
    s, wire = make_session(stt=stt)
    await s.start()
    await speak(s, 0.5)
    await silence(s, 0.14)                   # pause (128 ms) pero no fin de turno (160 ms)
    await wait_for(lambda: len(stt.calls) == 1)
    first = stt.calls[0]
    await speak(s, 0.5)                      # sigue hablando: el especulativo no vale
    await silence(s, 0.3)
    await wait_for(lambda: wire.of("metrics.turn"))
    assert len(wire.of("transcript.final")) == 1
    assert stt.calls[-1] > first             # la transcripcion final cubre todo el audio


def test_silero_real_model_ignores_silence_and_is_cheap():
    pytest.importorskip("onnxruntime")
    pytest.importorskip("faster_whisper")
    vad = StreamingVAD()
    silent = np.zeros(SR * 2, dtype=np.float32)
    vad.process(silent[:FRAME_SAMPLES])  # warm
    t0 = time.perf_counter()
    events = vad.process(silent)
    per_frame_ms = (time.perf_counter() - t0) * 1000 / (silent.size // FRAME_SAMPLES)
    assert events == []
    assert per_frame_ms < 2.0, per_frame_ms


# ----------------------------------------------------------------- session
@pytest.mark.asyncio
async def test_full_voice_turn_streams_audio_and_commits_context():
    s, wire = make_session()
    await s.start()
    assert wire.events[0]["audio"]["output_sample_rate"] == 16000
    await silence(s)
    await speak(s)
    await silence(s)
    await wait_for(lambda: "metrics.turn" in wire.types())
    t = wire.types()
    assert t.index("input_audio.speech_started") < t.index("input_audio.speech_stopped") \
        < t.index("transcript.final") < t.index("response.done")
    rid, seq, payload = unpack_audio(wire.audio[0])
    assert rid == 1 and seq == 0 and len(payload) > 0
    assert wire.of("response.done")[0]["text"] == "Hola, ¿qué tal? Bien."
    msgs = s.log.to_messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    m = wire.of("metrics.turn")[0]
    assert m["first_audio_ms"] is not None and m["gateway"]["p99_ms"] < 2.0
    await s.close()


@pytest.mark.asyncio
async def test_noise_shorter_than_min_speech_is_ignored():
    s, wire = make_session(min_speech_ms=400)
    await speak(s, 0.15)
    await silence(s)
    await asyncio.sleep(0.05)
    assert "response.created" not in wire.types()
    await s.close()


@pytest.mark.asyncio
async def test_barge_in_cancels_and_truncates_to_heard_audio():
    llm = FakeLLM(["Primera frase larga. ", "Segunda frase. ", "Tercera frase. ", "Cuarta."], delay=0.05)
    s, wire = make_session(llm=llm, tts=FakeTTS(chunk_ms=500, chunks=2))
    await speak(s)
    await silence(s)
    await wait_for(lambda: len(wire.audio) >= 2)
    await speak(s, 0.2)  # el usuario interrumpe
    await wait_for(lambda: "response.cancelled" in wire.types())
    c = wire.of("response.cancelled")[0]
    assert c["reason"] == "barge-in" and c["response_id"] == 1
    assert llm.cancelled == 1
    assert s._active is None
    # el cliente corto la reproduccion a los 400 ms
    await s.handle_event({"type": "response.truncate", "response_id": 1, "played_ms": 400})
    tr = wire.of("conversation.truncated")[0]
    assert tr["played_ms"] == 400
    assert "Segunda" not in tr["heard_text"]
    roles = [(t["role"], t["status"]) for t in s.log.history()]
    assert roles == [("user", "finalized"), ("assistant", "interrupted")]
    n_audio = len(wire.audio)
    await asyncio.sleep(0.2)
    assert len(wire.audio) == n_audio, "no debe llegar audio tras el barge-in"
    await s.close()


@pytest.mark.asyncio
async def test_pause_then_continue_reprocesses_merged_audio():
    stt = FakeSTT(text="quiero un cafe", delay=0.15)
    s, wire = make_session(stt=stt)
    await speak(s, 0.4)
    await silence(s)            # fin de turno -> STT lento en curso
    await wait_for(lambda: len(stt.calls) == 1)
    await speak(s, 0.4)        # sigue hablando antes de que haya audio
    await wait_for(lambda: "response.cancelled" in wire.types())
    assert wire.of("response.cancelled")[0]["reason"] == "user_continued"
    await silence(s)
    await wait_for(lambda: "response.done" in wire.types())
    assert stt.calls[-1] > stt.calls[0] * 1.5, "el segundo STT incluye el audio previo"
    assert [m["role"] for m in s.log.to_messages()] == ["user", "assistant"]
    await s.close()


@pytest.mark.asyncio
async def test_text_turns_keep_context_and_alternate_roles():
    llm = FakeLLM(["Vale."])
    s, wire = make_session(llm=llm)
    await s.handle_event({"type": "conversation.item.create", "text": "me llamo Ana"})
    await wait_for(lambda: len(wire.of("response.done")) == 1)
    await s.handle_event({"type": "conversation.item.create", "text": "¿cómo me llamo?"})
    await wait_for(lambda: len(wire.of("response.done")) == 2)
    msgs = llm.requests[-1]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == "me llamo Ana"
    await s.close()


@pytest.mark.asyncio
async def test_build_messages_merges_consecutive_user_turns():
    s, _ = make_session()
    s.log.add_user_turn("hola")
    msgs = s._build_messages("sigues ahi?")
    assert msgs[-1] == {"role": "user", "content": "hola sigues ahi?"}
    await s.close()


@pytest.mark.asyncio
async def test_llm_error_reports_and_recovers():
    class Boom:
        async def stream(self, messages, **kw):
            raise RuntimeError("LLM caido")
            yield  # pragma: no cover

    s, wire = make_session(llm=Boom())
    await s.handle_event({"type": "conversation.item.create", "text": "hola"})
    await wait_for(lambda: "error" in wire.types())
    assert wire.of("response.cancelled")[0]["reason"] == "error"
    assert s._active is None
    await s.close()


@pytest.mark.asyncio
async def test_session_update_and_ping():
    s, wire = make_session()
    await s.handle_event({"type": "session.update", "session": {"speed": 1.3, "vad": {"silence_ms": 500}}})
    assert s.config.speed == 1.3 and s.vad.config.silence_ms == 500
    await s.handle_event({"type": "ping", "t": 5})
    assert wire.events[-1] == {"type": "pong", "t": 5}
    await s.close()


# ------------------------------------------------------------ piper stream
def test_piper_streams_before_synthesis_finishes():
    from server.shared.backends.piper_backend import PiperBackend

    progress = {"produced": 0}

    class Chunk:
        def __init__(self):
            self.audio_float_array = np.zeros(4800, dtype=np.float32)

    class Voice:
        def synthesize(self, text, syn_config=None):
            for _ in range(3):
                progress["produced"] += 1
                yield Chunk()

    b = PiperBackend.__new__(PiperBackend)
    b._piper_voice = Voice()
    gen = b._synthesize_via_python("a. b. c.", 1.0)
    first, last = next(gen)
    assert len(first) == 4800 and not last
    assert progress["produced"] == 1, "debe emitir audio tras la primera frase"
    rest = list(gen)
    assert rest[-1][1] is True


def test_parakeet_output_spaces_glued_punctuation():
    from server.realtime.engines import _PUNCT_GLUE
    fix = lambda t: _PUNCT_GLUE.sub(r"\1 ", t)
    assert fix("Hola,¿cómo estás?") == "Hola, ¿cómo estás?"
    assert fix("Sí.¡Claro!") == "Sí. ¡Claro!"
    assert fix("Cuesta 3.5 euros.") == "Cuesta 3.5 euros."
