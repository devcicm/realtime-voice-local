"""Realtime voice (estilo OpenAI Realtime) 100 % local.

Un unico WebSocket full-duplex: el cliente envia PCM16 del microfono y
recibe PCM16 de la respuesta + eventos JSON. Todo el pipeline
(VAD -> STT -> LLM -> TTS, barge-in, contexto) corre en el servidor.

El nucleo (`RealtimeSession`) no depende de FastAPI: recibe callables
`send_json` / `send_bytes` y motores STT/LLM/TTS intercambiables, asi que
se puede montar en cualquier servidor ASGI o usarse desde tests.

    from server.realtime import RealtimeSession, SessionConfig
"""
from .protocol import PROTOCOL_VERSION, INPUT_SAMPLE_RATE, pack_audio, unpack_audio
from .session import RealtimeSession, SessionConfig

__all__ = [
    "PROTOCOL_VERSION",
    "INPUT_SAMPLE_RATE",
    "pack_audio",
    "unpack_audio",
    "RealtimeSession",
    "SessionConfig",
]
