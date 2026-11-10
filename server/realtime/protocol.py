"""Wire protocol de /ws/voice (v1).

Frames binarios
---------------
cliente -> servidor : PCM16 LE mono a 16 kHz, cualquier tamano (20 ms recomendado).
servidor -> cliente : cabecera 8 bytes `<II` (response_id, seq) + PCM16 LE mono
                      a `output_sample_rate` (anunciado en `session.created`).

Frames de texto: JSON con campo "type".

cliente -> servidor
    session.update           {session: {...SessionConfig parcial}}
    input_audio.commit       fuerza fin de turno (push-to-talk)
    input_audio.clear        descarta el audio en curso
    conversation.item.create {text}   turno de usuario por texto
    response.cancel          cancela la respuesta activa
    playback.position        {response_id, played_ms}   (~cada 100 ms)
    response.truncate        {response_id, played_ms}   (tras cortar audio)
    conversation.clear
    ping

servidor -> cliente
    session.created / session.updated {session, audio}
    input_audio.speech_started {audio_ms, response_id?}
    input_audio.speech_stopped {audio_ms}
    transcript.final        {response_id, text}
    response.created        {response_id}
    response.text.delta     {response_id, delta}
    response.segment        {response_id, index, text, start_ms}
    response.done           {response_id, text, audio_ms}
    response.cancelled      {response_id, reason, audio_ms}
    conversation.truncated  {response_id, heard_text, played_ms}
    metrics.turn            {response_id, ...ms por etapa}
    error                   {message}
    pong
"""
from __future__ import annotations

import struct

PROTOCOL_VERSION = 1
INPUT_SAMPLE_RATE = 16000
AUDIO_HEADER = struct.Struct("<II")

CLIENT_EVENTS = frozenset({
    "session.update",
    "input_audio.commit",
    "input_audio.clear",
    "conversation.item.create",
    "response.cancel",
    "playback.position",
    "response.truncate",
    "conversation.clear",
    "ping",
})

SERVER_EVENTS = frozenset({
    "session.created",
    "session.updated",
    "input_audio.speech_started",
    "input_audio.speech_stopped",
    "transcript.final",
    "response.created",
    "response.text.delta",
    "response.segment",
    "response.done",
    "response.cancelled",
    "conversation.truncated",
    "metrics.turn",
    "error",
    "pong",
})


def pack_audio(response_id: int, seq: int, pcm16: bytes) -> bytes:
    return AUDIO_HEADER.pack(response_id & 0xFFFFFFFF, seq & 0xFFFFFFFF) + pcm16


def unpack_audio(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < AUDIO_HEADER.size:
        raise ValueError("audio frame shorter than header")
    rid, seq = AUDIO_HEADER.unpack_from(frame)
    return rid, seq, frame[AUDIO_HEADER.size:]
