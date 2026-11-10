"""
KokoroBackend - Wrapper del VoiceGenerator actual bajo la interfaz TTSBackend.

Kokoro-82M: 82M parametros, ONNX-compatible, 24kHz audio.
Mejor para: PCs modernas con CPU decente o GPU pequena.
Acento espanol: solo latinoamericano (3 voces oficiales).
"""

from pathlib import Path
from typing import List, Optional, Iterator, Tuple
import numpy as np

from .base import TTSBackend, VoiceInfo, BackendCapabilities

try:
    from ..generator import VoiceGenerator, create_voice_generator
    KOKORO_AVAILABLE = True
except ImportError:
    KOKORO_AVAILABLE = False


# Catalogo de voces oficiales de Kokoro-82M v1.1
# Fuente: https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md
KOKORO_VOICES = {
    # American English
    "af_heart":      ("a", "en", "f", "high",   "American English female 'Heart' (default Kokoro, highest quality)"),
    "af_bella":      ("a", "en", "f", "high",   "American English female 'Bella' (A- grade)"),
    "af_nicole":     ("a", "en", "f", "medium", "American English female 'Nicole' (B- grade)"),
    "af_sarah":      ("a", "en", "f", "medium", "American English female 'Sarah'"),
    "af_sky":        ("a", "en", "f", "low",    "American English female 'Sky'"),
    "am_adam":       ("a", "en", "m", "medium", "American English male 'Adam'"),
    "am_michael":    ("a", "en", "m", "medium", "American English male 'Michael'"),
    # British English
    "bf_emma":       ("b", "en", "f", "medium", "British English female 'Emma' (B- grade)"),
    "bf_isabella":   ("b", "en", "f", "medium", "British English female 'Isabella'"),
    "bm_george":     ("b", "en", "m", "medium", "British English male 'George'"),
    "bm_lewis":      ("b", "en", "m", "medium", "British English male 'Lewis'"),
    # Spanish (SOLO LATINOAMERICANO)
    "ef_dora":       ("e", "es", "f", "low",    "Spanish female 'Dora' (LATINO AMERICAN ONLY, no castellano)"),
    "em_alex":       ("e", "es", "m", "low",    "Spanish male 'Alex' (LATINO AMERICAN ONLY)"),
    "em_santa":      ("e", "es", "m", "low",    "Spanish male 'Santa' (LATINO AMERICAN ONLY)"),
    # French
    "ff_siwis":      ("f", "fr", "f", "low",    "French female 'Siwis'"),
    # Hindi
    "hf_alpha":      ("h", "hi", "f", "low",    "Hindi female 'Alpha'"),
    "hm_omega":      ("h", "hi", "m", "low",    "Hindi male 'Omega'"),
    # Italian
    "if_sara":       ("i", "it", "f", "low",    "Italian female 'Sara'"),
    "im_nicola":     ("i", "it", "m", "low",    "Italian male 'Nicola'"),
    # Japanese
    "jf_alpha":      ("j", "ja", "f", "low",    "Japanese female 'Alpha'"),
    "jm_kumo":       ("j", "ja", "m", "low",    "Japanese male 'Kumo'"),
    # Portuguese (Brazilian)
    "pf_dora":       ("p", "pt", "f", "low",    "Brazilian Portuguese female 'Dora'"),
    "pm_alex":       ("p", "pt", "m", "low",    "Brazilian Portuguese male 'Alex'"),
    # Chinese
    "zf_xiaoxiao":   ("z", "zh", "f", "low",    "Mandarin Chinese female 'Xiaoxiao'"),
    "zm_yunjian":    ("z", "zh", "m", "low",    "Mandarin Chinese male 'Yunjian'"),
}

LANG_NAMES = {
    "a": "American English", "b": "British English",
    "e": "Spanish (Latin American)", "f": "French",
    "h": "Hindi", "i": "Italian", "j": "Japanese",
    "p": "Brazilian Portuguese", "z": "Mandarin Chinese",
}


class KokoroBackend(TTSBackend):
    """Backend TTS usando Kokoro-82M (modelo actual del proyecto)."""

    def __init__(self, models_dir=None, voices_dir=None, voice_downloader=None):
        self._models_dir = Path(models_dir) if models_dir else None
        self._voices_dir = Path(voices_dir) if voices_dir else None
        self._voice_downloader = voice_downloader
        self._generator: Optional[VoiceGenerator] = None
        self._current_voice: Optional[str] = None
        self._model_path: Optional[str] = None
        self._initialized = False
        self._caps = BackendCapabilities(
            name="kokoro",
            min_ram_gb=2.0,
            min_cpu_generation=4,        # Haswell (4th gen) recomendado
            needs_gpu=False,
            gpu_vram_gb=0.0,
            supports_streaming=True,
            supports_voice_cloning=False, # NO soporta
            supports_blending=True,       # Implementado en feats via tensor averaging
            max_audio_length_s=30,
            realtime_factor_cpu=1.5,      # 1.5x realtime en CPU moderno
            install_size_mb=350,          # ~350MB modelo + deps
            model_size_mb=350,            # kokoro.pth ~310MB
            license="Apache-2.0",
        )

    @property
    def name(self) -> str:
        return "kokoro"

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._caps

    def initialize(self, model_path="kokoro.pth", voice_name="ef_dora", **kwargs) -> str:
        if not KOKORO_AVAILABLE:
            raise RuntimeError(
                "VoiceGenerator no disponible. Revisa que src/utils/generator.py exista."
            )
        self._generator = create_voice_generator(
            models_dir=self._models_dir,
            voices_dir=self._voices_dir,
        )
        result = self._generator.initialize(model_path, voice_name)
        self._model_path = model_path
        self._current_voice = voice_name
        self._initialized = True
        return result

    def is_initialized(self) -> bool:
        return self._initialized and self._generator and self._generator.is_initialized()

    def list_voices(self) -> List[VoiceInfo]:
        # Voces del catalogo hardcoded
        result = []
        for vname, (lang_code, lang_iso, gender, quality, desc) in KOKORO_VOICES.items():
            result.append(VoiceInfo(
                name=vname,
                language=lang_iso,
                language_name=LANG_NAMES.get(lang_code, lang_iso),
                gender=gender,
                quality=quality,
                description=desc,
                needs_gpu=False,
                needs_reference_audio=False,
                sample_rate=24000,
            ))
        # Sumar voces locales adicionales (blends, descargas HF)
        if self._initialized and self._generator:
            try:
                local = self._generator.list_available_voices()
                for v in local:
                    if v in KOKORO_VOICES:
                        continue
                    # Inferir language code del prefijo
                    lang_code = v[0] if v else "?"
                    result.append(VoiceInfo(
                        name=v,
                        language=lang_code,
                        language_name=LANG_NAMES.get(lang_code, "Custom"),
                        gender=v.split("_")[1][0] if "_" in v and len(v.split("_")) > 1 else "?",
                        quality="custom",
                        description="Voz local (blend o descarga HuggingFace)",
                        needs_gpu=False,
                        sample_rate=24000,
                    ))
            except Exception:
                pass
        return result

    def get_voice(self, name: str) -> Optional[VoiceInfo]:
        for v in self.list_voices():
            if v.name == name:
                return v
        return None

    def change_voice(self, name: str, **kwargs) -> str:
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado. Llama initialize() primero.")
        result = self._generator.change_voice(name)
        self._current_voice = name
        return result

    def current_voice(self) -> Optional[str]:
        return self._current_voice

    def generate(self, text, lang=None, speed=1.0, **kwargs):
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        return self._generator.generate(text, lang=lang, speed=speed, **kwargs)

    def generate_streaming(self, text, lang=None, speed=1.0, chunk_size=4800, **kwargs):
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        return self._generator.generate_streaming(
            text, lang=lang, speed=speed, chunk_size=chunk_size
        )

    def get_sample_rate(self) -> int:
        return 24000
