"""APIKeyMiddleware: HTTP y WebSocket, cabeceras/query, exenciones y CORS."""
import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.shared.auth import APIKeyMiddleware

KEY = "clave-secreta-123"


def make_app(keys):
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/tts")
    def tts():
        return {"ok": True}

    @app.websocket("/ws/tts")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"type": "connected"})
        await websocket.close()

    app.add_middleware(APIKeyMiddleware, keys=keys)
    app.add_middleware(CORSMiddleware, allow_origins=["https://app.example"],
                       allow_methods=["*"], allow_headers=["*"])
    return TestClient(app)


@pytest.fixture
def client():
    return make_app([KEY, "otra-clave"])


def test_sin_claves_todo_abierto():
    c = make_app([])
    assert c.post("/tts").status_code == 200
    with c.websocket_connect("/ws/tts") as ws:
        assert ws.receive_json()["type"] == "connected"


def test_http_sin_clave_401(client):
    r = client.post("/tts")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.parametrize("kwargs", [
    {"headers": {"Authorization": f"Bearer {KEY}"}},
    {"headers": {"authorization": "bearer otra-clave"}},
    {"headers": {"X-API-Key": KEY}},
    {"params": {"api_key": KEY}},
])
def test_http_clave_valida(client, kwargs):
    assert client.post("/tts", **kwargs).status_code == 200


def test_http_clave_invalida(client):
    assert client.post("/tts", headers={"Authorization": "Bearer mala"}).status_code == 401
    assert client.post("/tts", headers={"Authorization": KEY}).status_code == 401  # sin "Bearer"


def test_health_exento(client):
    assert client.get("/health").status_code == 200


def test_cors_preflight_y_401_con_cabeceras(client):
    pre = client.options("/tts", headers={"Origin": "https://app.example",
                                          "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200
    r = client.post("/tts", headers={"Origin": "https://app.example"})
    assert r.status_code == 401
    assert r.headers["access-control-allow-origin"] == "https://app.example"


def test_ws_sin_clave_rechazado(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws/tts"):
            pass
    assert e.value.code == 1008


@pytest.mark.parametrize("kwargs,url", [
    ({"headers": {"Authorization": f"Bearer {KEY}"}}, "/ws/tts"),
    ({}, f"/ws/tts?api_key={KEY}"),
])
def test_ws_con_clave(client, kwargs, url):
    with client.websocket_connect(url, **kwargs) as ws:
        assert ws.receive_json()["type"] == "connected"
