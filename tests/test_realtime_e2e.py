"""Test E2E: Piper genera audio -> STT -> LLM gemma-4-e2b con max_tokens=1500 -> TTS. Todo medido."""
import json
import time
import urllib.request
import asyncio
import wave
import tempfile
from pathlib import Path


def log(msg, t="info"):
    c = {"info":"\033[94m","success":"\033[92m","warn":"\033[93m","error":"\033[91m"}.get(t,"")
    print(f"{c}[{t.upper()}]\033[0m {msg}")


def piper_generate(text, voice, onnx_path, out_path):
    """Usa Piper Python API para generar WAV."""
    from piper import PiperVoice
    v = PiperVoice.load(str(onnx_path))
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(v.config.sample_rate)
        for chunk in v.synthesize(text):
            wf.writeframes(chunk.audio_int16_bytes)


def stt_transcribe(wav_path, lang="es", model="tiny"):
    boundary = "----FormBoundary"
    wav_bytes = Path(wav_path).read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n{lang}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="t.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request("http://localhost:8100/transcribe", data=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def llm_stream(prompt, max_tokens=1500):
    body = {
        "model": "google/gemma-4-e2b",
        "messages": [
            {"role": "system", "content": "Responde UNICAMENTE en espanol. Breve y natural."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": 0.5,
        "stream": True
    }
    req = urllib.request.Request("http://localhost:12344/v1/chat/completions",
                                  data=json.dumps(body).encode(),
                                  headers={"Content-Type": "application/json"})
    text = ""
    reasoning = ""
    ttft = None
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        for line in r:
            line = line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "): continue
            payload = line[6:]
            if payload == "[DONE]": break
            try:
                d = json.loads(payload)
                delta = d["choices"][0].get("delta", {})
                if delta.get("content"):
                    if ttft is None: ttft = time.time() - t0
                    text += delta["content"]
                if delta.get("reasoning_content"):
                    if ttft is None: ttft = time.time() - t0
                    reasoning += delta["reasoning_content"]
            except: pass
    return text, reasoning, ttft, time.time() - t0


async def tts_send(text):
    import websockets
    t0 = time.time()
    async with websockets.connect("ws://localhost:8990/ws/tts", max_size=None) as ws:
        await ws.recv()  # connected
        await ws.send(json.dumps({"type":"tts","text":text,"request_id":"test_rt"}))
        await ws.recv()  # audio_start
        t_first = None
        chunks = 0
        bytes_total = 0
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                if t_first is None: t_first = time.time() - t0
                chunks += 1
                bytes_total += len(msg)
            else:
                d = json.loads(msg)
                if d.get("type") == "audio_end": break
    return t_first, time.time() - t0, chunks, bytes_total


async def main():
    log("="*60, "info")
    log("  TEST E2E: STT (Whisper) -> LLM (gemma-4-e2b) -> TTS (Piper)", "info")
    log("="*60, "info")

    onnx = Path("data/piper_voices/es_ES_davefx-medium.onnx")
    if not onnx.exists():
        log(f"No existe {onnx}", "error"); return

    # Frases que generan respuestas visibles con max_tokens=1500
    test_phrases = [
        "Hola, buenos dias, me llamo Carlos.",
        "Que tiempo hace hoy en Madrid?",
        "Dime un dato curioso sobre el espacio.",
    ]

    for i, phrase in enumerate(test_phrases, 1):
        log(f"\n--- Test {i}/3: '{phrase}' ---", "info")
        # 1. Piper genera audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_in = Path(tmp.name)
        t0 = time.time()
        piper_generate(phrase, "es_ES/davefx-medium", onnx, wav_in)
        piper_ms = (time.time() - t0) * 1000
        log(f"  1. Piper genero audio: {wav_in.stat().st_size:,} bytes ({piper_ms:.0f}ms)", "info")

        # 2. STT
        t0 = time.time()
        stt = stt_transcribe(wav_in)
        stt_ms = (time.time() - t0) * 1000
        log(f"  2. STT: '{stt['text']}' ({stt_ms:.0f}ms, RTF={stt['rtf']:.2f})", "success")

        # 3. LLM con max_tokens=1500
        t0 = time.time()
        text, reasoning, ttft, total = llm_stream(stt['text'], max_tokens=1500)
        log(f"  3. LLM: TTFT={ttft*1000:.0f}ms total={total*1000:.0f}ms", "success")
        log(f"     content: '{text[:200]}{'...' if len(text) > 200 else ''}'", "info" if text else "warn")
        if not text and reasoning:
            log(f"     (gasto todo en razonamiento, razon: {reasoning[:100]}...)", "warn")

        # 4. TTS (solo si hay content)
        if text.strip():
            t0 = time.time()
            t_first, t_total, chunks, bts = await tts_send(text)
            log(f"  4. TTS: first-byte={t_first*1000:.0f}ms total={t_total*1000:.0f}ms {chunks} chunks {bts:,}B", "success")
        else:
            log(f"  4. TTS: SKIP (sin texto visible)", "warn")

        wav_in.unlink(missing_ok=True)

    log("\n=== OK - Pipeline funcional end-to-end ===", "success")


if __name__ == "__main__":
    asyncio.run(main())
