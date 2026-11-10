"""Tests for the STT server's Result-pattern helpers."""
import io
import wave
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from server.stt.stt_server import app, _decode_audio
from server.shared.result import Ok, Err


def _make_wav_bytes(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Build a minimal silent WAV file."""
    import numpy as np
    n_samples = int(duration_s * sample_rate)
    audio = np.zeros(n_samples, dtype=np.float32)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        # Convert float32 to int16 for WAV
        audio_int16 = (audio * 32767).astype("int16")
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def test_decode_audio_ok():
    wav = _make_wav_bytes(duration_s=0.5, sample_rate=16000)
    res = _decode_audio(wav)
    assert res.is_ok()
    audio, sr, duration = res.value
    assert sr == 16000
    assert abs(duration - 0.5) < 0.05


def test_decode_audio_too_long():
    wav = _make_wav_bytes(duration_s=120.0, sample_rate=16000)
    res = _decode_audio(wav)
    assert res.is_err()
    assert res.error[0] == 413  # payload too large
    assert "demasiado largo" in res.error[1]


def test_decode_audio_invalid_bytes():
    res = _decode_audio(b"not a wav file")
    assert res.is_err()
    assert res.error[0] == 400  # bad request


def test_transcribe_endpoint_includes_metrics():
    """Smoke test that the endpoint still works and exposes /metrics."""
    client = TestClient(app)
    res = client.get("/metrics")
    assert res.status_code == 200
    body = res.json()
    assert "metrics" in body
    assert "recent_traces" in body


def test_transcribe_endpoint_rejects_empty_audio():
    client = TestClient(app)
    res = client.post(
        "/transcribe",
        files={"audio": ("empty.wav", b"", "audio/wav")},
        data={"model": "tiny"},
    )
    assert res.status_code == 400
    assert "vacio" in res.json()["detail"]
