"""Smoke test end-to-end del harness: STT -> LLM -> TTS."""
import json
import time
import wave
import urllib.request
import tempfile
import asyncio
from pathlib import Path
import sys


def log(msg, type_="info"):
    colors = {"info": "\033[94m", "success": "\033[92m", "warn": "\033[93m", "error": "\033[91m"}
    nc = "\033[0m"
    print(f"{colors.get(type_, '')}[{type_.upper()}]{nc} {msg}")


async def test_pipeline():
    log("=== END-TO-END HARNESS VOICE TEST ===", "info")
    log("Simula: hablar (audio generado por Piper) -> STT -> LLM -> TTS WebSocket", "info")
    print()

    # 1. Generar audio de "habla" usando Piper
    onnx_path = Path("data/piper_voices/es_ES_davefx-medium.onnx")
    if not onnx_path.exists():
        log(f"No existe {onnx_path}", "error")
        return

    from piper import PiperVoice
    test_phrases = [
        ("saludo", "Hola, buenos dias, como estas?"),
        ("pregunta", "Cual es la capital de Francia?"),
        ("largo", "Me podrias explicar en pocas palabras que es Python y para que se usa?"),
    ]

    voice = PiperVoice.load(str(onnx_path))

    for label, text in test_phrases:
        log(f"--- Test: {label}: '{text[:50]}{'...' if len(text) > 50 else ''}' ---", "info")

        # Generar audio
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(voice.config.sample_rate)
            for chunk in voice.synthesize(text):
                wf.writeframes(chunk.audio_int16_bytes)

        wav_bytes = wav_path.read_bytes()
        log(f"  Audio: {len(wav_bytes)} bytes, {len(wav_bytes)/32044:.1f}s", "info")

        # 2. STT
        boundary = "----FormBoundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="language"\r\n\r\n'
            f"es\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\n'
            f"tiny\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="audio"; filename="test.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            "http://localhost:8100/transcribe",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=30) as r:
            stt_result = json.loads(r.read())
        stt_elapsed = time.time() - t0
        log(f"  STT: '{stt_result['text']}' ({stt_elapsed:.2f}s, RTF={stt_result['rtf']:.2f})", "success")

        # 3. LLM
        llm_body = json.dumps({
            "model": "google/gemma-4-e2b",
            "messages": [
                {"role": "system", "content": "Responde en espanol, maximo 2 frases."},
                {"role": "user", "content": stt_result["text"]}
            ],
            "max_tokens": 200,
            "temperature": 0.7,
            "stream": True
        }).encode()
        llm_req = urllib.request.Request(
            "http://localhost:12344/v1/chat/completions",
            data=llm_body,
            headers={"Content-Type": "application/json"},
        )
        t0 = time.time()
        with urllib.request.urlopen(llm_req, timeout=60) as r:
            llm_text = ""
            ttft = None
            for line in r:
                line = line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "): continue
                payload = line[6:]
                if payload == "[DONE]": break
                try:
                    d = json.loads(payload)
                    delta = d["choices"][0].get("delta", {})
                    text = delta.get("content") or delta.get("reasoning_content") or ""
                    if text:
                        if ttft is None:
                            ttft = time.time() - t0
                        llm_text += text
                except: pass
        llm_elapsed = time.time() - t0
        log(f"  LLM: TTFT={ttft*1000:.0f}ms, total={llm_elapsed*1000:.0f}ms", "success")
        log(f"  LLM response: '{llm_text[:200]}{'...' if len(llm_text) > 200 else ''}'", "info")

        # 4. TTS WebSocket
        try:
            import websockets
        except ImportError:
            log("  TTS skip: websockets no instalado", "warn")
            wav_path.unlink(missing_ok=True)
            continue

        async with websockets.connect("ws://localhost:8990/ws/tts", max_size=None) as ws:
            connect_msg = json.loads(await ws.recv())
            log(f"  TTS WS: backend={connect_msg.get('backend')}", "info")

            t0 = time.time()
            await ws.send(json.dumps({"type": "tts", "text": llm_text, "request_id": f"e2e_{label}"}))
            audio_start = json.loads(await ws.recv())
            chunks = 0
            total_bytes = 0
            t_first_byte = None
            while True:
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    if t_first_byte is None: t_first_byte = time.time() - t0
                    chunks += 1
                    total_bytes += len(msg)
                else:
                    d = json.loads(msg)
                    if d.get("type") == "audio_end": break
            t_total = time.time() - t0
            log(f"  TTS: first-byte={t_first_byte*1000:.0f}ms, total={t_total*1000:.0f}ms, {chunks} chunks, {total_bytes:,} bytes", "success")

        wav_path.unlink(missing_ok=True)
        print()

    log("=== TODOS LOS TESTS OK ===", "success")


if __name__ == "__main__":
    try:
        asyncio.run(test_pipeline())
    except KeyboardInterrupt:
        log("Interrumpido", "warn")
