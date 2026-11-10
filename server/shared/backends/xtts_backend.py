"""
XTTSBackend - TTS con voice cloning de Coqui. SOLO para PCs con GPU.

XTTS v2 (Coqui TTS):
  - 467M parametros
  - Multilingual (17 idiomas, castellano nativo)
  - Voice cloning con 6 segundos de audio de referencia
  - 24kHz audio
  - REQUIERE GPU con >=4GB VRAM (ideal 6GB+)

Instalacion:
  pip install TTS
  # Mas info: https://github.com/coqui-ai/TTS

NO recomendado para PCs con Intel 2da gen (sin GPU moderna).
"""

from pathlib import Path
from typing import List, Optional, Iterator, Tuple
import numpy as np

from .base import TTSBackend, VoiceInfo, BackendCapabilities

try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

try:
    from TTS.api import TTS as CoquiTTS
    XTTS_AVAILABLE = True
except ImportError:
    XTTS_AVAILABLE = False


# Voces demo oficiales de XTTS + posibilidad de cloning con audio custom
XTTS_DEFAULT_VOICES = [
    # Las voces demo son speakers del dataset VCTK. Se accede por ruta a .wav
    # xtts v2 viene con samples en tts/tts/models/xtts/samples/
    "Claribel Dervla", "Daisy Studious", "Gracie Wise", "Tammie Ema",
    "Alison Dietlinde", "Ana Florence", "Annmarie Nele", "Asya Anara",
    "Brenda Stern", "Gitta Nikolina", "Henriette Usha", "Sofia Hellen",
    "Tammy Grit", "Tanja Adelina", "Vjollca Johnnie", "Andrew Chipper",
    "Badr Odai", "Dionisio Schuyler", "Royston Min", "Viktor Eka",
    "Abrahan Mack", "Adde Michal", "Baldur Sanjin", "Craig Gutsy",
    "Damien Black", "Gilberto Mathias", "Ilkin Urbano", "Kazuhiko Atallah",
    "Ludvig Milivoj", "Suad Qasim", "Torcull Diarmuid", "Viktor Menelaos",
    "Zacharie Aimilios", "Nova Hogarth", "Maja Ruoho", "Uta Obando",
    "Lidiya Szekeres", "Chandra MacFarland", "Szofi Granger", "Camilla Holmstrom",
    "Lilya Stainthorpe", "Zofija Kendrick", "Narelle Moon", "Barbora MacLean",
    "Alexandra Hisako", "Damayanthi Posey", "Wendy Sirietta", "Keren Higgins",
    "Aretha Panicker", "Asuka Yanis", "Antonella Chiaramonte", "Melina Middle",
    "Gianina Caro", "Ilya Badr", "Marisol Rico", "Regina Klimek",
    "Brielle Broadbent", "Elicia Enders", "Marita Lerer", "Heta Desai",
]


class XTTSBackend(TTSBackend):
    """
    Backend TTS usando XTTS v2 de Coqui.
    Solo recomendado si tienes GPU con >=4GB VRAM.
    """

    def __init__(self, model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                 reference_audio=None, gpu=True):
        self._model_name = model_name
        self._reference_audio = reference_audio  # path a wav para voice cloning
        self._use_gpu = gpu and TORCH_AVAILABLE and torch.cuda.is_available()
        self._tts: Optional[CoquiTTS] = None
        self._current_voice: Optional[str] = None
        self._sample_rate: int = 24000
        self._initialized = False
        self._caps = BackendCapabilities(
            name="xtts",
            min_ram_gb=8.0,
            min_cpu_generation=6,        # Skylake (6th gen) como minimo si usas CPU
            needs_gpu=True,
            gpu_vram_gb=4.0,             # minimo 4GB, ideal 6GB
            supports_streaming=True,
            supports_voice_cloning=True,  # Con 6s de audio de referencia
            supports_blending=False,
            max_audio_length_s=30,        # XTTS tiene limite ~30s
            realtime_factor_cpu=0.1,      # CPU es 10x mas lento
            install_size_mb=2000,         # ~2GB dependencias
            model_size_mb=1800,           # ~1.8GB modelo
            license="CPML (non-commercial)",
        )

    @property
    def name(self) -> str:
        return "xtts"

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._caps

    def initialize(self, voice_name=None, reference_audio=None, **kwargs) -> str:
        if not XTTS_AVAILABLE:
            raise RuntimeError(
                "Coqui TTS no instalado. Instala con:\n"
                "  pip install TTS\n"
                "OJO: requiere ~2GB de espacio y compilador C++ en algunas plataformas."
            )
        if not self._use_gpu:
            print("WARNING: XTTS sin GPU. Sera ~10x mas lento. No recomendado.")
        # Cargar modelo
        self._tts = CoquiTTS(self._model_name, gpu=self._use_gpu)
        if reference_audio is not None:
            self._reference_audio = reference_audio
            self._current_voice = f"cloned:{Path(reference_audio).stem}"
        elif voice_name is not None:
            self._current_voice = voice_name
        else:
            self._current_voice = XTTS_DEFAULT_VOICES[0]
        self._sample_rate = self._tts.synthesizer.output_sample_rate
        self._initialized = True
        return f"Loaded XTTS v2 (gpu={self._use_gpu}): {self._current_voice}"

    def is_initialized(self) -> bool:
        return self._initialized and self._tts is not None

    def list_voices(self) -> List[VoiceInfo]:
        result = []
        for v in XTTS_DEFAULT_VOICES:
            result.append(VoiceInfo(
                name=v,
                language="multi",
                language_name="Multilingual",
                gender="?",
                quality="high",
                description=f"XTTS demo speaker: {v}",
                needs_gpu=True,
                needs_reference_audio=False,
                sample_rate=24000,
            ))
        if self._reference_audio:
            result.append(VoiceInfo(
                name=f"cloned:{Path(self._reference_audio).stem}",
                language="multi",
                language_name="Cloned (custom)",
                gender="?",
                quality="high",
                description=f"Voice cloned from {self._reference_audio}",
                needs_gpu=True,
                needs_reference_audio=True,
                sample_rate=24000,
            ))
        return result

    def get_voice(self, name: str) -> Optional[VoiceInfo]:
        for v in self.list_voices():
            if v.name == name:
                return v
        return None

    def change_voice(self, name: str, reference_audio=None, **kwargs) -> str:
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        if reference_audio is not None:
            self._reference_audio = reference_audio
            self._current_voice = f"cloned:{Path(reference_audio).stem}"
        else:
            self._current_voice = name
        return f"Voice changed to: {self._current_voice}"

    def current_voice(self) -> Optional[str]:
        return self._current_voice

    def generate(self, text, lang="es", speed=1.0, **kwargs):
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        if not self._current_voice:
            raise RuntimeError("No hay voz seleccionada. Llama change_voice() primero.")
        # XTTS usa speed como 'speed' parameter
        try:
            import tempfile
            import soundfile as sf
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            self._tts.tts_to_file(
                text=text,
                file_path=tmp_path,
                speaker=self._current_voice if not self._current_voice.startswith("cloned:") else None,
                speaker_wav=self._reference_audio if self._current_voice.startswith("cloned:") else None,
                language=lang or "es",
                speed=speed,
            )
            audio, sr = sf.read(tmp_path)
            Path(tmp_path).unlink(missing_ok=True)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return audio.astype(np.float32), []
        except Exception as e:
            print(f"XTTS error: {e}")
            return None, []

    def generate_streaming(self, text, lang="es", speed=1.0, chunk_size=4800, **kwargs):
        # XTTS no soporta streaming nativo, simulamos fragmentando la salida
        audio, _ = self.generate(text, lang=lang, speed=speed)
        if audio is None or len(audio) == 0:
            return
        for i in range(0, len(audio), chunk_size):
            yield audio[i:i+chunk_size], i + chunk_size >= len(audio)

    def get_sample_rate(self) -> int:
        return self._sample_rate
