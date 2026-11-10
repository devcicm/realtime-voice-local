"""POST /tts y /v1/audio/speech con un backend falso (sin modelos reales)."""
import io
import wave

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import server.tts.websocket_server as ws


class FakeBackend:
    SR = 16000

    def __init__(self):
        self.voice = "es_ES/fake"
        self.changes = []

    def is_initialized(self):
        return True

    def get_voice(self, name):
        return object() if name in ("es_ES/fake", "es_ES/otra") else None

    def current_voice(self):
        return self.voice

    def change_voice(self, name, **kwargs):
        self.changes.append(name)
        self.voice = name
        return name

    def get_sample_rate(self):
        return self.SR

    def generate_streaming(self, text, lang=None, speed=1.0, chunk_size=4800, **kwargs):
        # 0.25 s de tono en dos trozos
        t = np.arange(self.SR // 4) / self.SR
        tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        yield tone[:2000], False
        yield tone[2000:], True


@pytest.fixture
def client(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(ws.tts_server, "backend", fake)
    # Sin "with": no se ejecuta el lifespan (no carga modelos reales).
    return TestClient(ws.app), fake


def test_tts_devuelve_wav_valido(client):
    c, fake = client
    r = c.post("/tts", json={"text": "Hola"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["x-sample-rate"] == "16000"
    with wave.open(io.BytesIO(r.content)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 16000
        assert w.getnframes() == FakeBackend.SR // 4
    assert fake.changes == []  # misma voz: no se reinicializa


def test_tts_pcm_y_cambio_de_voz(client):
    c, fake = client
    r = c.post("/tts", json={"text": "Hola", "voice": "es_ES/otra", "format": "pcm"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/L16"
    assert len(r.content) == FakeBackend.SR // 4 * 2
    assert fake.changes == ["es_ES/otra"]
    assert r.headers["x-voice"] == "es_ES/otra"


def test_openai_speech_ignora_voz_openai(client):
    c, fake = client
    r = c.post("/v1/audio/speech",
               json={"model": "tts-1", "input": "Hola", "voice": "alloy", "response_format": "wav"})
    assert r.status_code == 200
    assert r.content[:4] == b"RIFF"
    assert fake.changes == []


@pytest.mark.parametrize("body,code", [
    ({"text": ""}, 400),
    ({"text": "x", "format": "mp3"}, 400),
    ({"text": "x", "voice": "no_existe"}, 404),
    ({"text": "x" * 5001}, 413),
    ({"text": "x", "speed": "rapido"}, 400),
])
def test_tts_errores(client, body, code):
    c, _ = client
    assert c.post("/tts", json=body).status_code == code


def test_tts_stream_wav(client):
    c, _ = client
    r = c.post("/tts", json={"text": "Hola", "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["x-sample-rate"] == "16000"
    assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WAVE"
    assert len(r.content) == 44 + FakeBackend.SR // 4 * 2  # cabecera + PCM16


def test_tts_stream_pcm_y_openai(client):
    c, _ = client
    r = c.post("/v1/audio/speech", json={"input": "Hola", "response_format": "pcm", "stream": True})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/L16"
    assert len(r.content) == FakeBackend.SR // 4 * 2


def test_tts_acepta_cp1252(client):
    c, _ = client
    body = '{"text": "canción"}'.encode("cp1252")
    r = c.post("/tts", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200


def test_tts_sin_backend_503(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(ws.tts_server, "backend", None)
    assert c.post("/tts", json={"text": "Hola"}).status_code == 503
