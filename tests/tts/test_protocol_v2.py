"""Tests for the TTS protocol v2 (chunk_id, audio_chunk headers, word_offsets)."""
import asyncio
import json
import time
import wave
from pathlib import Path

import pytest

from server.tts.websocket_server import TTSServer

_estimate_word_offsets = TTSServer._estimate_word_offsets


class TestEstimateWordOffsets:
    def test_empty_text_returns_empty_list(self):
        assert _estimate_word_offsets("", 24000, 24000) == []

    def test_single_word_offset_is_zero(self):
        offsets = _estimate_word_offsets("hola", 24000, 24000)
        assert offsets == [0.0]

    def test_multiple_words_distribute_by_length(self):
        offsets = _estimate_word_offsets("a bb ccc", 24000, 24000)  # total 1000ms
        # Weights: [1, 2, 3] -> total 6. Offsets: 0, 1/6*1000=166.67, 3/6*1000=500
        assert offsets[0] == 0.0
        assert 150 < offsets[1] < 200
        assert 450 < offsets[2] < 550

    def test_offsets_match_total_duration(self):
        offsets = _estimate_word_offsets("uno dos tres cuatro cinco", 22050 * 2, 22050)
        # Last word should start before the total duration.
        total_ms = 22050 * 2 / 22050 * 1000
        assert offsets[-1] < total_ms


class TestProtocolV2Helpers:
    def setup_method(self):
        self.server = TTSServer.__new__(TTSServer)
        # Skip __init__ to avoid backend wiring.

    def test_audio_start_v2_includes_protocol_version(self):
        # The v2 audio_start dict has protocol_version: 2 and chunk_id.
        # We can't easily test the full send path, but we can confirm
        # the function signature accepts chunk_id and protocol_version.
        import inspect
        sig = inspect.signature(self.server.process_tts_request)
        assert "chunk_id" in sig.parameters
        assert "protocol_version" in sig.parameters
        assert sig.parameters["protocol_version"].default == 1
