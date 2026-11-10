"""E2E de latencia de /ws/voice contra el stack real (TTS 8990 + LM Studio).

    python tests/test_voice_ws_e2e.py            # imprime tabla de latencias
    python -m pytest tests/test_voice_ws_e2e.py  # se salta si el stack no corre

Escenario:
  1. Turno de voz: envia data/smoke_input.wav a ritmo real (frames 20 ms) y
     mide cada etapa (metrics.turn).
  2. Barge-in: cuando llega el primer audio de la respuesta, vuelve a hablar
     y mide cuanto tarda el servidor en cortar (response.cancelled).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

URL = os.getenv("VOICE_WS_URL", "ws://localhost:8990/ws/voice")
WAV = Path(__file__).resolve().parents[1] / "data" / "smoke_input.wav"
FRAME = 320  # 20 ms a 16 kHz


def _stack_up() -> bool:
    try:
        urllib.request.urlopen("http://localhost:8990/health", timeout=2)
        return True
    except Exception:
        return False


def _load_pcm16() -> bytes:
    import soundfile as sf
    audio, sr = sf.read(str(WAV), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()


async def _stream(ws, pcm: bytes, realtime: bool = True) -> None:
    step = FRAME * 2
    for i in range(0, len(pcm), step):
        await ws.send(pcm[i:i + step])
        if realtime:
            await asyncio.sleep(0.02)


async def run(verbose: bool = True) -> dict:
    import websockets

    speech = _load_pcm16()
    silence = bytes(16000 * 2)  # 1 s
    out: dict = {}
    async with websockets.connect(URL, max_size=None, open_timeout=90) as ws:
        created = json.loads(await ws.recv())
        assert created["type"] == "session.created", created
        out["session"] = created

        # ---- 1) turno normal
        t_send = time.perf_counter()
        await _stream(ws, speech)
        t_end_speech = time.perf_counter()
        await _stream(ws, silence)
        events, first_audio_at = [], None
        while True:
            msg = await asyncio.wait_for(ws.recv(), 60)
            if isinstance(msg, bytes):
                first_audio_at = first_audio_at or time.perf_counter()
                continue
            ev = json.loads(msg)
            events.append(ev)
            if ev["type"] == "error":
                raise RuntimeError(ev)
            if ev["type"] == "metrics.turn":
                break
        out["turn"] = next(e for e in events if e["type"] == "metrics.turn")
        out["transcript"] = next((e["text"] for e in events if e["type"] == "transcript.final"), "")
        out["reply"] = next((e["text"] for e in events if e["type"] == "response.done"), "")
        out["client_first_audio_after_speech_ms"] = (
            round((first_audio_at - t_end_speech) * 1000, 1) if first_audio_at else None)

        # ---- 2) barge-in
        await _stream(ws, speech)
        await _stream(ws, silence[: 16000])  # 0.5 s -> fin de turno
        rid = None
        while rid is None:
            msg = await asyncio.wait_for(ws.recv(), 60)
            if isinstance(msg, bytes):
                rid = int.from_bytes(msg[:4], "little")
        t_interrupt = time.perf_counter()
        sender = asyncio.create_task(_stream(ws, speech))
        cancelled = None
        while cancelled is None:
            msg = await asyncio.wait_for(ws.recv(), 30)
            if isinstance(msg, str):
                ev = json.loads(msg)
                if ev["type"] == "response.cancelled" and ev["response_id"] == rid:
                    cancelled = ev
        out["barge_in_client_ms"] = round((time.perf_counter() - t_interrupt) * 1000, 1)
        out["barge_in_server"] = cancelled
        await ws.send(json.dumps({"type": "response.truncate", "response_id": rid, "played_ms": 300}))
        await sender
        while True:
            msg = await asyncio.wait_for(ws.recv(), 30)
            if isinstance(msg, str) and json.loads(msg)["type"] == "conversation.truncated":
                out["truncated"] = json.loads(msg)
                break
        out["wall_s"] = round(time.perf_counter() - t_send, 2)

    if verbose:
        m = out["turn"]
        print(f"\nTranscripcion : {out['transcript']!r}")
        print(f"Respuesta     : {out['reply'][:120]!r}")
        print("\nEtapa                          ms")
        for k in ("stt_ms", "llm_first_token_ms", "first_clause_ms", "tts_first_chunk_ms",
                  "first_audio_ms", "llm_total_ms", "total_ms"):
            print(f"  {k:<28} {m.get(k)}")
        print(f"  {'cliente: fin voz->1er audio':<28} {out['client_first_audio_after_speech_ms']}")
        print(f"  {'gateway p50 / p99 por frame':<28} {m['gateway']['p50_ms']} / {m['gateway']['p99_ms']}")
        print(f"  {'barge-in (servidor)':<28} {out['barge_in_server'].get('interrupt_ms')}")
        print(f"  {'barge-in (cliente, ida+vuelta)':<28} {out['barge_in_client_ms']}")
        print(f"  heard_text tras truncar 300 ms: {out['truncated']['heard_text']!r}")
    return out


@pytest.mark.skipif(not _stack_up(), reason="stack TTS no esta corriendo en :8990")
def test_voice_ws_e2e_latency():
    pytest.importorskip("websockets")
    out = asyncio.run(run(verbose=True))
    m = out["turn"]
    assert m["gateway"]["p50_ms"] < 2.0  # p99 depende de la carga de CPU (STT/LLM/TTS comparten CPU)
    assert m["first_audio_ms"] is not None
    assert out["barge_in_server"]["interrupt_ms"] < 50
    assert out["truncated"]["played_ms"] == 300


if __name__ == "__main__":
    asyncio.run(run())
