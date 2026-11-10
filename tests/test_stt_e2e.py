"""Test end-to-end: Piper genera audio -> STT lo transcribe."""
import json
import time
import urllib.request
import wave
import tempfile
from pathlib import Path

# 2. Generar texto conocido
test_text = "Hola, esto es una prueba de transcripcion de audio."
print(f"Texto de prueba: {test_text!r}")

# 3. Usar Piper (Python API) para generar el WAV
onnx_path = Path("data/piper_voices/es_ES_davefx-medium.onnx")
if not onnx_path.exists():
    print(f"ERROR: {onnx_path} no existe")
    exit(1)

from piper import PiperVoice
print(f"Cargando Piper voice: {onnx_path.name}")
voice = PiperVoice.load(str(onnx_path))

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
    output_wav = Path(tmp.name)

print(f"Generando audio con Piper...")
with wave.open(str(output_wav), "wb") as wav_file:
    wav_file.setnchannels(1)
    wav_file.setsampwidth(2)  # 16-bit
    wav_file.setframerate(voice.config.sample_rate)
    for chunk in voice.synthesize(test_text):
        wav_file.writeframes(chunk.audio_int16_bytes)

print(f"Audio generado: {output_wav.stat().st_size} bytes")

# 4. Manda al STT server
import soundfile as sf
audio, sr = sf.read(str(output_wav))
duration_s = len(audio) / sr
print(f"Duracion: {duration_s:.2f}s @ {sr}Hz")

# 5. POST al STT
boundary = "----TestBoundary"
with open(output_wav, "rb") as f:
    audio_bytes = f.read()

body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="language"\r\n\r\n'
    f"es\r\n"
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="audio"; filename="test.wav"\r\n'
    f"Content-Type: audio/wav\r\n\r\n"
).encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode()

req = urllib.request.Request(
    "http://localhost:8100/transcribe",
    data=body,
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    method="POST",
)
print("Enviando al STT server...")
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read())
    elapsed = time.time() - t0
    print(f"\n=== RESULTADO ===")
    print(f"Status: 200 (en {elapsed:.2f}s)")
    print(f"Texto transcrito: {result['text']!r}")
    print(f"Idioma detectado: {result['language']} (prob={result['language_probability']})")
    print(f"Modelo: {result['model']}, RTF: {result['rtf']}")
    print(f"\nMatch con original? {test_text.lower() in result['text'].lower() or result['text'].lower() in test_text.lower()}")
except Exception as e:
    print(f"FAIL: {e}")

# Cleanup
output_wav.unlink(missing_ok=True)
