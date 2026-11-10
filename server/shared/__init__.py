"""Public surface of server.shared — compatibility shim.

Only the names still used by the active server modules are re-exported.
Legacy Kokoro-era symbols (VoiceGenerator, load_voice, etc.) are gone
as of Phase 8 (legacy Kokoro purge).
"""
from .config import settings
from .llm import filter_response, get_ai_response
from .audio_utils import save_audio_file
from .audio_converter import AudioConverter, AudioFormat, create_audio_converter
from .text_chunker import TextChunker
from .voice_downloader import VoiceDownloader, create_voice_downloader

__all__ = [
    "settings",
    "filter_response",
    "get_ai_response",
    "save_audio_file",
    "AudioConverter",
    "AudioFormat",
    "create_audio_converter",
    "TextChunker",
    "VoiceDownloader",
    "create_voice_downloader",
]
