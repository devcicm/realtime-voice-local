"""End-to-end test del stack: STT -> LLM (proxy) -> TTS.

Generates an audio file with Piper, sends to STT, sends the transcript
to the LLM through our proxy, then synthesizes the response with TTS.
Every request propagates an X-Trace-Id so the flow is visible in the
server logs.

Usage: python tests/smoke_e2e.py
"""
import asyncio
import io
import json
import time
import wave
from pathlib import Path

import httpx
import websockets


LM_PROXY = "http://localhost:8990/llm/v1"
STT_URL = "http://localhost:8100/transcribe"
TTS_WS = "ws://localhost:8990/ws/tts"
PIPER_VOICE = "data/piper_voices/es_ES_davefx-medium.onnx"
TRACE = f"smoke-{int(time.time())}"


def log(*a):
    print("[smoke]", *a, flush=True)


def synth_input_wav(path: Path, text: str = "Hola, ¿cómo estás?"):
    """Generate a test audio file with Piper directly (synchronous)."""
    from piper import PiperVoice
    v = PiperVoice.load(PIPER_VOICE)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(v.config.sample_rate)
        for chunk in v.synthesize(text):
            wf.writeframes(chunk.audio_int16_bytes)
    return path.stat().st_size


async def stt_transcribe(wav_path: Path) -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        with open(wav_path, "rb") as f:
            r = await c.post(
                STT_URL,
                files={"audio": (wav_path.name, f, "audio/wav")},
                data={"language": "es", "model": "tiny"},
                headers={"X-Trace-Id": TRACE},
            )
        r.raise_for_status()
        data = r.json()
        return data["text"]


async def llm_chat(text: str, model: str = "google/gemma-3n-e4b") -> str:
    """Send the user text to the LLM through our proxy."""
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{LM_PROXY}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Responde en espanol, maximo 2 frases."},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 120,
                "stream": False,
                "temperature": 0.7,
            },
            headers={"X-Trace-Id": TRACE},
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


async def tts_synth(text: str) -> int:
    """Send text to TTS, count audio bytes received."""
    bytes_received = 0
    chunks = 0
    async with websockets.connect(TTS_WS, additional_headers={"X-Trace-Id": TRACE}) as ws:
        # First message: connected (info from server)
        connected = json.loads(await ws.recv())
        log(f"TTS connected: backend={connected.get('backend')} voice={connected.get('voice')}")
        # Send tts request
        await ws.send(json.dumps({
            "type": "tts",
            "text": text,
            "request_id": TRACE,
            "chunk_id": f"c-{TRACE}",
            "protocol_version": 2,
        }))
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                bytes_received += len(msg)
                chunks += 1
            else:
                d = json.loads(msg)
                if d.get("type") == "audio_end":
                    log(f"TTS end: chunks={chunks} bytes={bytes_received} duration={d.get('duration_ms')}ms")
                    log(f"  word_offsets_ms: {d.get('word_offsets_ms', [])[:5]}")
                    break
                if d.get("type") == "error":
                    raise RuntimeError(f"TTS error: {d}")
    return bytes_received


async def main():
    log(f"trace_id = {TRACE}")

    # 1. Generate test audio (sync, in a thread so we don't block the loop).
    wav_path = Path("data/smoke_input.wav")
    wav_path.parent.mkdir(exist_ok=True)
    log(f"generating test audio with Piper -> {wav_path}")
    size = await asyncio.to_thread(synth_input_wav, wav_path)
    log(f"  audio size: {size} bytes")

    # 2. STT.
    log("STT /transcribe ...")
    t0 = time.time()
    text = await stt_transcribe(wav_path)
    log(f"  STT: '{text}' ({time.time()-t0:.2f}s)")

    # 3. LLM via proxy.
    log("LLM /chat/completions via proxy ...")
    t0 = time.time()
    llm_response = await llm_chat(text)
    log(f"  LLM: '{llm_response}' ({time.time()-t0:.2f}s)")

    # 4. TTS WebSocket.
    log(f"TTS WebSocket (synthesize LLM response) ...")
    t0 = time.time()
    n_bytes = await tts_synth(llm_response)
    log(f"  TTS: {n_bytes} bytes in {time.time()-t0:.2f}s")

    # 5. Save the audio for inspection.
    log("Re-running TTS to save the audio to disk for inspection ...")
    out_path = Path("data/smoke_output.wav")
    out_path.parent.mkdir(exist_ok=True)
    pcm_chunks = []

    async with websockets.connect(TTS_WS, additional_headers={"X-Trace-Id": TRACE + "-save"}) as ws:
        await ws.recv()  # connected
        await ws.send(json.dumps({
            "type": "tts",
            "text": llm_response,
            "request_id": TRACE + "-save",
            "chunk_id": "c-save",
            "protocol_version": 2,
        }))
        sample_rate = 22050
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                pcm_chunks.append(msg)
            else:
                d = json.loads(msg)
                if d.get("type") == "audio_start":
                    sample_rate = d.get("sample_rate", sample_rate)
                if d.get("type") == "audio_end":
                    break
    # Combine all PCM chunks and write as WAV
    pcm = b"".join(pcm_chunks)
    if len(pcm) > 0:
        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        log(f"  saved audio: {out_path} ({out_path.stat().st_size} bytes)")

    log("DONE.")


if __name__ == "__main__":
    asyncio.run(main())
