"""Cliente de línea de comandos para /ws/tts (ws:// o wss://).

Conecta, (opcional) configura voz/velocidad, manda el texto y recibe el audio
en streaming (PCM float32). Por defecto lo REPRODUCE EN VIVO mientras llega,
sin guardar nada; con -o además (o solo) lo guarda como WAV.

Uso:
    python client/tts_ws_client.py "Hola, esto es una prueba"            # suena en vivo
    python client/tts_ws_client.py "Hola" --voice es_ES/davefx-medium --speed 1.0
    python client/tts_ws_client.py "Hola" -o hola.wav --no-play          # solo guardar
    python client/tts_ws_client.py "Hola" --url wss://mi-host/ws/tts --api-key CLAVE

API key: --api-key o variable de entorno TTS_API_KEY (se envía como
"Authorization: Bearer"). Solo hace falta si el servidor tiene API_KEYS.

Reproducción en vivo: requiere `pip install sounddevice` (PortAudio).
"""
import argparse
import asyncio
import json
import os
import queue
import ssl
import sys
import threading
import time
import uuid
import wave

import numpy as np

try:
    import websockets
    import websockets.exceptions  # noqa: F401  (no se carga solo en websockets 10)
except ImportError:
    sys.exit("Falta la librería: pip install websockets")


class LivePlayer:
    """Reproduce PCM float32 a medida que llega. El audio se escribe desde un
    hilo propio para no bloquear la recepción del WebSocket."""

    def __init__(self, sample_rate: int):
        import sounddevice as sd
        self._q: "queue.Queue[np.ndarray | None]" = queue.Queue()
        self._stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                       latency="low")
        self._stream.start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while (chunk := self._q.get()) is not None:
            self._stream.write(chunk.reshape(-1, 1))

    def feed(self, chunk: np.ndarray):
        self._q.put(chunk)

    def close(self):
        """Espera a que termine de sonar lo encolado y cierra."""
        self._q.put(None)
        self._thread.join()
        self._stream.stop()
        self._stream.close()


async def synthesize(url, text, voice=None, speed=None, insecure=False, timeout=60.0,
                     on_start=None, on_chunk=None, api_key=None):
    """Recibe el audio en streaming. Llama on_start(sample_rate) y
    on_chunk(np.float32) según llega. Devuelve (audio_completo, sample_rate, info)."""
    ssl_ctx = None
    if url.startswith("wss://"):
        ssl_ctx = ssl.create_default_context()
        if insecure:  # certificado autofirmado
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    t0 = time.perf_counter()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    # websockets >= 14 usa additional_headers; las versiones anteriores, extra_headers.
    hdr_kw = "additional_headers" if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers"
    rejected = tuple(getattr(websockets.exceptions, n) for n in ("InvalidStatus", "InvalidStatusCode")
                     if hasattr(websockets.exceptions, n))
    try:
        ws = await websockets.connect(url, max_size=None, ssl=ssl_ctx, **{hdr_kw: headers})
    except rejected as e:
        code = getattr(getattr(e, "response", None), "status_code", None) or getattr(e, "status_code", "?")
        hint = " (API key ausente o inválida: --api-key / TTS_API_KEY)" if code in (401, 403) else ""
        raise RuntimeError(f"el servidor rechazó la conexión: HTTP {code}{hint}") from None
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        t_conn = time.perf_counter()

        if voice or speed:
            cfg = {"type": "config", "format": "pcm"}
            if voice:
                cfg["voice"] = voice
            if speed:
                cfg["speed"] = speed
            await ws.send(json.dumps(cfg))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if ack.get("type") == "error":
                raise RuntimeError(f"config rechazada: {ack.get('message', ack)}")

        req_id = uuid.uuid4().hex[:8]
        t_send = time.perf_counter()
        await ws.send(json.dumps({"type": "tts", "text": text, "request_id": req_id}))

        sr, chunks, t_first = None, [], None
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout)
            if isinstance(msg, bytes):
                if t_first is None:
                    t_first = time.perf_counter()
                chunk = np.frombuffer(msg, dtype="<f4")
                chunks.append(chunk)
                if on_chunk:
                    on_chunk(chunk)
                continue
            data = json.loads(msg)
            kind = data.get("type")
            if kind == "audio_start":
                sr = int(data["sample_rate"])
                if data.get("format", "pcm") != "pcm":
                    raise RuntimeError(f"formato inesperado: {data.get('format')}")
                if on_start:
                    on_start(sr)
            elif kind == "audio_end":
                break
            elif kind == "error":
                raise RuntimeError(f"error del servidor: {data.get('message', data)}")
    finally:
        await ws.close()

    t_end = time.perf_counter()
    audio = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    info = {
        "backend": hello.get("backend"),
        "voice": voice or hello.get("voice"),
        "chunks": len(chunks),
        "connect_ms": (t_conn - t0) * 1000,
        "first_audio_ms": ((t_first or t_end) - t_send) * 1000,
        "total_ms": (t_end - t_send) * 1000,
        "audio_ms": len(audio) / sr * 1000 if sr else 0,
    }
    return audio, sr, info


def save_wav(path, audio, sr):
    pcm16 = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())


def main():
    ap = argparse.ArgumentParser(description="Cliente WebSocket del servidor TTS")
    ap.add_argument("text")
    ap.add_argument("--url", default="ws://127.0.0.1:8990/ws/tts")
    ap.add_argument("--voice")
    ap.add_argument("--speed", type=float)
    ap.add_argument("-o", "--out", help="guardar también como WAV")
    ap.add_argument("--no-play", action="store_true", help="no reproducir")
    ap.add_argument("--api-key", default=os.getenv("TTS_API_KEY"), help="o variable TTS_API_KEY")
    ap.add_argument("--insecure", action="store_true", help="wss:// con certificado autofirmado")
    args = ap.parse_args()
    if args.no_play and not args.out:
        ap.error("con --no-play indica -o archivo.wav")

    player = None

    def on_start(sr):
        nonlocal player
        if not args.no_play:
            player = LivePlayer(sr)

    def on_chunk(chunk):
        if player:
            player.feed(chunk)

    try:
        audio, sr, info = asyncio.run(synthesize(
            args.url, args.text, args.voice, args.speed, args.insecure,
            on_start=on_start, on_chunk=on_chunk, api_key=args.api_key))
    except (OSError, RuntimeError, asyncio.TimeoutError) as e:
        sys.exit(f"ERROR: {e}")

    print(f"{info['audio_ms']:.0f} ms de audio @ {sr} Hz, voz={info['voice']}, {info['chunks']} chunks | "
          f"conexión {info['connect_ms']:.0f} ms, primer audio {info['first_audio_ms']:.0f} ms, "
          f"recibido todo en {info['total_ms']:.0f} ms")
    if args.out:
        save_wav(args.out, audio, sr)
        print(f"guardado {args.out}")
    if player:
        player.close()


if __name__ == "__main__":
    main()
