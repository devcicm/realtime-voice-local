"""Adaptador ASGI (Starlette/FastAPI) para RealtimeSession.

    from server.realtime.ws import serve_websocket

    @app.websocket("/ws/voice")
    async def voice(ws):
        await serve_websocket(ws, stt=..., llm=..., tts=..., config=SessionConfig(...))

No importa FastAPI a nivel de modulo: solo usa la interfaz de
`starlette.websockets.WebSocket` (accept / receive / send_text / send_bytes).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

from .session import RealtimeSession, SessionConfig

logger = logging.getLogger(__name__)


async def serve_websocket(websocket, *, stt, llm, tts,
                          config: Optional[SessionConfig] = None) -> None:
    await websocket.accept()

    async def send_json(obj: dict) -> None:
        await websocket.send_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

    session = RealtimeSession(send_json, websocket.send_bytes, stt=stt, llm=llm, tts=tts,
                              config=config, session_id=f"rt-{uuid.uuid4().hex[:8]}")
    try:
        await session.start()
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                await session.feed_audio(data)
                continue
            text = msg.get("text")
            if text is None:
                continue
            try:
                ev = json.loads(text)
            except ValueError:
                await send_json({"type": "error", "message": "JSON invalido"})
                continue
            if isinstance(ev, dict):
                await session.handle_event(ev)
    except Exception as e:  # desconexion abrupta, etc.
        if type(e).__name__ not in ("WebSocketDisconnect", "ConnectionClosedOK", "ConnectionClosedError"):
            logger.warning("realtime ws error: %r", e)
    finally:
        await session.close()
