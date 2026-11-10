"""Smoke test WebSocket contra servidor Piper.

Mide: first-byte latency, total bytes recibidos, chunks recibidos.
"""
import asyncio
import json
import time
import sys

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets")
    sys.exit(1)


WS_URL = "ws://localhost:8990/ws/tts"
TEST_PHRASES = [
    ("corta", "Hola."),
    ("media", "Buenos días, ¿cómo está usted hoy? Espero que tenga un excelente día."),
    ("larga", "El servidor TTS multi-backend permite cambiar entre Kokoro y Piper "
             "manteniendo una interfaz uniforme. Este audio se está generando en "
             "tiempo real con chunks PCM streamed por WebSocket."),
]


async def test_phrase(label, text):
    print(f"\n--- {label}: '{text[:60]}{'...' if len(text) > 60 else ''}' ---")
    t_start = time.time()
    async with websockets.connect(WS_URL, max_size=None) as ws:
        # Recibir mensaje "connected"
        connect_msg = json.loads(await ws.recv())
        print(f"  backend={connect_msg.get('backend')}, voice={connect_msg.get('voice')}")

        # Enviar TTS request
        t_send = time.time()
        await ws.send(json.dumps({
            "type": "tts",
            "text": text,
            "request_id": f"test_{label}",
        }))

        # Recibir audio_start
        start_msg = json.loads(await ws.recv())
        if start_msg.get("type") != "audio_start":
            print(f"  ERROR: esperaba audio_start, recibí {start_msg}")
            return
        print(f"  audio_start: voice={start_msg['voice']}, sr={start_msg['sample_rate']}, fmt={start_msg['format']}")

        # Recibir chunks
        chunks = 0
        total_bytes = 0
        t_first_byte = None
        end_msg = None
        while True:
            msg = await ws.recv()
            if isinstance(msg, bytes):
                if t_first_byte is None:
                    t_first_byte = time.time() - t_send
                chunks += 1
                total_bytes += len(msg)
            else:
                data = json.loads(msg)
                if data.get("type") == "audio_end":
                    end_msg = data
                    break
                if data.get("type") == "error":
                    print(f"  ERROR del server: {data.get('code')} - {data.get('message')}")
                    return

        t_total = time.time() - t_send
        audio_dur = total_bytes / 2 / 22050  # 16-bit PCM @ 22050Hz
        rtf = (t_total - t_first_byte) / audio_dur if audio_dur > 0 else 0

        print(f"  first-byte : {t_first_byte*1000:.0f}ms")
        print(f"  total      : {t_total*1000:.0f}ms")
        print(f"  chunks     : {chunks}")
        print(f"  bytes      : {total_bytes:,}")
        print(f"  audio dur  : {audio_dur:.2f}s")
        print(f"  RTF (sintesis) : {rtf:.3f}x realtime")


async def main():
    print("=" * 60)
    print("  Smoke test WebSocket -> Piper (es_ES/davefx-medium)")
    print("=" * 60)
    for label, text in TEST_PHRASES:
        try:
            await test_phrase(label, text)
        except Exception as e:
            print(f"  FAILED: {e}")
    print("\n=== OK ===")


if __name__ == "__main__":
    asyncio.run(main())
