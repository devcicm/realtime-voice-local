"""
PiperBackend - TTS local basado en VITS/ONNX. OPTIMO para PCs modestas.

Piper TTS:
  - Modelos ONNX pequenos (15-120MB)
  - CPU-friendly, corre en Raspberry Pi 4
  - Castellano nativo: es_ES (Espana) y es_MX (Mexico)
  - NO requiere GPU
  - Streaming nativo con chunks PCM

Instalacion:
  pip install piper-tts

Voces disponibles en: https://huggingface.co/rhasspy/piper-voices
"""

import os
import sys
import wave
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Iterator, Tuple
import numpy as np

from .base import TTSBackend, VoiceInfo, BackendCapabilities


# Catalogo de voces verificadas en HuggingFace
# https://huggingface.co/rhasspy/piper-voices/tree/main/es
PIPER_VOICES = {
    # Castellano (Espana) - NATIVO. Inventario verificado en HF rhasspy/piper-voices
    "es_ES/davefx-medium":       ("es", "es-ES", "m", "medium", "Castellano Spain - DaveFX (male, 63MB)"),
    "es_ES/sharvard-medium":     ("es", "es-ES", "m", "medium", "Castellano Spain - Sharvard (male)"),
    "es_ES/carlfm-x_low":        ("es", "es-ES", "m", "x_low",  "Castellano Spain - Carlfm (male, 15MB)"),
    "es_ES/mls_10246-low":       ("es", "es-ES", "m", "low",    "Castellano Spain - MLS 10246 (low memory)"),
    "es_ES/mls_9972-low":        ("es", "es-ES", "m", "low",    "Castellano Spain - MLS 9972 (low memory)"),
    # Latinoamericano
    "es_MX/ald-medium":          ("es", "es-MX", "m", "medium", "Spanish Mexico - Ald (male)"),
    "es_MX/claude-high":         ("es", "es-MX", "f", "high",   "Spanish Mexico - Claude (female)"),
    # Otros idiomas
    "en_US/lessac-medium":       ("en", "en-US", "f", "medium", "American English - Lessac (female)"),
    "en_US/lessac-low":          ("en", "en-US", "f", "low",    "American English - Lessac (female, low memory)"),
    "en_US/amy-low":             ("en", "en-US", "f", "low",    "American English - Amy (low memory)"),
    "en_GB/alba-medium":         ("en", "en-GB", "f", "medium", "British English - Alba (female)"),
    "fr_FR/siwis-medium":        ("fr", "fr-FR", "f", "medium", "French - Siwis (female)"),
    "pt_BR/faber-medium":        ("pt", "pt-BR", "m", "medium", "Brazilian Portuguese - Faber (male)"),
    "it_IT/riccardo-medium":     ("it", "it-IT", "m", "medium", "Italian - Riccardo (male)"),
    "de_DE/thorsten-medium":     ("de", "de-DE", "m", "medium", "German - Thorsten (male)"),
}

# Donde se guardan los modelos descargados
PIPER_DEFAULT_MODEL_DIR = Path.home() / ".local" / "share" / "piper" / "voices"


class PiperBackend(TTSBackend):
    """
    Backend TTS usando Piper (VITS/ONNX).
    El unico con voces castellanas NATIVAS.
    """

    def __init__(self, model_dir=None, use_cli_fallback=False):
        """
        Args:
            model_dir: directorio donde estan los .onnx (default: ~/.local/share/piper/voices)
            use_cli_fallback: si Piper Python lib no esta instalado, usar el binario CLI
                              (default False: prefiere Python API)
        """
        self._model_dir = Path(model_dir) if model_dir else PIPER_DEFAULT_MODEL_DIR
        self._model_dir.mkdir(parents=True, exist_ok=True)
        self._use_cli_fallback = use_cli_fallback
        self._piper_voice = None  # PiperVoice instance (Python API)
        self._current_voice: Optional[str] = None
        self._sample_rate: int = 22050
        self._initialized = False
        self._caps = BackendCapabilities(
            name="piper",
            min_ram_gb=1.0,
            min_cpu_generation=2,        # Sandy Bridge (2nd gen) - corre pero lento
            needs_gpu=False,
            gpu_vram_gb=0.0,
            supports_streaming=True,
            supports_voice_cloning=False,
            supports_blending=False,
            max_audio_length_s=60,
            realtime_factor_cpu=0.7,      # 0.7x realtime en CPU medio
            install_size_mb=80,
            model_size_mb=63,             # modelo medium tipico
            license="MIT",
        )

    @property
    def name(self) -> str:
        return "piper"

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._caps

    def _try_load_python_api(self):
        """Intenta cargar piper Python API. Retorna PiperVoice o None."""
        try:
            from piper import PiperVoice
            return PiperVoice
        except ImportError:
            return None

    def _download_voice(self, voice_key: str) -> Tuple[Path, Path]:
        """Descarga un .onnx y .onnx.json desde HuggingFace rhasspy/piper-voices."""
        if voice_key not in PIPER_VOICES:
            raise ValueError(f"Voz desconocida: {voice_key}")
        # voice_key formato: "es_ES/davefx-medium" o "es_ES/carlfm-x_low"
        lang_country, name_quality = voice_key.split("/")
        # El path HF usa el formato es/es_ES/<name>/<quality>/es_ES-<name>-<quality>.onnx
        # donde quality es 'medium', 'low', 'x_low', 'high' (con guion bajo en x_low)
        voice_name, quality = name_quality.rsplit("-", 1)
        rel_path = f"{lang_country[:2]}/{lang_country}/{voice_name}/{quality}/{lang_country}-{name_quality}.onnx"
        url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{rel_path}"
        json_url = url + ".json"
        out_onnx = self._model_dir / f"{voice_key.replace('/', '_')}.onnx"
        out_json = self._model_dir / f"{voice_key.replace('/', '_')}.onnx.json"
        if out_onnx.exists() and out_json.exists():
            return out_onnx, out_json
        import urllib.request
        print(f"  Downloading {voice_key} from {url}...")
        # Timeout 120s por archivo (modelos de ~63MB)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                with open(out_onnx, "wb") as f:
                    f.write(resp.read())
        except Exception as e:
            raise RuntimeError(
                f"Timeout/Error descargando {url} en 120s: {e}"
            )
        try:
            with urllib.request.urlopen(json_url, timeout=30) as resp:
                with open(out_json, "wb") as f:
                    f.write(resp.read())
        except Exception as e:
            raise RuntimeError(
                f"Timeout/Error descargando {json_url} en 30s: {e}"
            )
        return out_onnx, out_json

    def initialize(self, voice_name="es_ES/davefx-medium", **kwargs) -> str:
        # Verificar que el modelo existe, si no descargarlo
        if voice_name not in PIPER_VOICES:
            raise ValueError(
                f"Voz Piper desconocida: {voice_name}. "
                f"Disponibles: {list(PIPER_VOICES.keys())}"
            )
        onnx_path, json_path = self._download_voice(voice_name)

        # Intentar cargar via Python API
        PiperVoice = self._try_load_python_api()
        if PiperVoice is not None and not self._use_cli_fallback:
            self._piper_voice = PiperVoice.load(str(onnx_path))
            self._sample_rate = self._piper_voice.config.sample_rate
            self._current_voice = voice_name
            self._initialized = True
            return f"Loaded piper (Python API): {voice_name}"

        # Fallback: CLI
        piper_bin = self._find_piper_binary()
        if piper_bin is None:
            raise RuntimeError(
                "Piper no instalado. Instala con: pip install piper-tts\n"
                "O usa la imagen Docker: docker run -it rhasspy/piper"
            )
        self._piper_binary = piper_bin
        self._current_voice = voice_name
        self._initialized = True
        # sample rate para medium/high piper es 22050
        self._sample_rate = 22050
        return f"Loaded piper (CLI): {voice_name}"

    def _find_piper_binary(self) -> Optional[Path]:
        """Busca el binario piper en PATH o en locations conocidos."""
        import shutil
        bin_path = shutil.which("piper")
        if bin_path:
            return Path(bin_path)
        # Windows: %LOCALAPPDATA%\Programs\piper\
        if sys.platform == "win32":
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "piper" / "piper.exe",
                Path("C:/Program Files/piper/piper.exe"),
            ]
            for c in candidates:
                if c.exists():
                    return c
        return None

    def is_initialized(self) -> bool:
        return self._initialized

    def list_voices(self) -> List[VoiceInfo]:
        result = []
        for vname, (lang_iso, locale, gender, quality, desc) in PIPER_VOICES.items():
            result.append(VoiceInfo(
                name=vname,
                language=lang_iso,
                language_name=locale,
                gender=gender,
                quality=quality,
                description=desc,
                needs_gpu=False,
                sample_rate=22050,
            ))
        return result

    def get_voice(self, name: str) -> Optional[VoiceInfo]:
        for v in self.list_voices():
            if v.name == name:
                return v
        return None

    def change_voice(self, name: str, **kwargs) -> str:
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        return self.initialize(voice_name=name)

    def current_voice(self) -> Optional[str]:
        return self._current_voice

    def _piper_chunks(self, text: str, speed: float):
        """Iterador perezoso sobre los AudioChunk de Piper (una frase por chunk)."""
        length_scale = 1.0 / max(speed, 0.1)
        try:
            from piper.config import SynthesisConfig
            return iter(self._piper_voice.synthesize(
                text, syn_config=SynthesisConfig(length_scale=length_scale)))
        except (ImportError, TypeError):
            try:  # API vieja con length_scale como kwarg directo
                return iter(self._piper_voice.synthesize(text, length_scale=length_scale))
            except TypeError:
                return iter(self._piper_voice.synthesize(text))

    @staticmethod
    def _chunk_to_float(audio_chunk) -> Optional[np.ndarray]:
        if getattr(audio_chunk, "audio_float_array", None) is not None:
            return np.asarray(audio_chunk.audio_float_array, dtype=np.float32)
        if getattr(audio_chunk, "audio_int16_array", None) is not None:
            return audio_chunk.audio_int16_array.astype(np.float32) / 32768.0
        if getattr(audio_chunk, "audio_int16_bytes", None) is not None:
            return np.frombuffer(audio_chunk.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return None

    def _synthesize_via_python(self, text: str, speed: float,
                               chunk_size: int = 4800) -> Iterator[Tuple[np.ndarray, bool]]:
        """Sintesis streaming con la Python API de piper 1.4+.

        Emite audio en cuanto Piper termina cada frase (antes se hacia
        list(synthesize()) y el primer audio esperaba al texto completo).
        """
        buffer = np.zeros(0, dtype=np.float32)
        for audio_chunk in self._piper_chunks(text, speed):
            arr = self._chunk_to_float(audio_chunk)
            if arr is None or arr.size == 0:
                continue
            buffer = arr if buffer.size == 0 else np.concatenate([buffer, arr])
            while buffer.size >= chunk_size:
                yield buffer[:chunk_size], False
                buffer = buffer[chunk_size:]
        yield buffer, True

    def warmup(self) -> None:
        """Una sintesis corta para cargar sesiones ONNX y caches (primer audio rapido)."""
        if self._piper_voice is None:
            return
        for _ in self._synthesize_via_python("Hola.", 1.0):
            pass

    def _synthesize_via_cli(self, text: str, speed: float) -> Tuple[Optional[np.ndarray], List[str]]:
        """Sintesis usando CLI piper. Escribe a un WAV temporal y lee."""
        if not hasattr(self, "_piper_binary") or self._piper_binary is None:
            return None, []
        onnx_path, _ = self._download_voice(self._current_voice)
        length_scale = 1.0 / max(speed, 0.1)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            cmd = [
                str(self._piper_binary),
                "--model", str(onnx_path),
                "--output_file", str(tmp_path),
                "--length_scale", str(length_scale),
            ]
            proc = subprocess.run(
                cmd, input=text, text=True,
                capture_output=True, timeout=60,
            )
            if proc.returncode != 0 or not tmp_path.exists():
                return None, []
            import soundfile as sf
            audio, sr = sf.read(str(tmp_path))
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            return audio.astype(np.float32), []
        finally:
            tmp_path.unlink(missing_ok=True)

    def generate(self, text, lang=None, speed=1.0, **kwargs):
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        if self._piper_voice is not None:
            # Sintesis via Python API
            all_chunks = []
            for chunk, _ in self._synthesize_via_python(text, speed):
                if len(chunk) > 0:
                    all_chunks.append(chunk)
            if not all_chunks:
                return None, []
            return np.concatenate(all_chunks), []
        else:
            return self._synthesize_via_cli(text, speed)

    def generate_streaming(self, text, lang=None, speed=1.0, chunk_size=4800, **kwargs):
        if not self.is_initialized():
            raise RuntimeError("Backend no inicializado.")
        if self._piper_voice is not None:
            yield from self._synthesize_via_python(text, speed, chunk_size=chunk_size)
        else:
            # CLI no soporta streaming, generar todo y fragmentar
            audio, _ = self._synthesize_via_cli(text, speed)
            if audio is None or len(audio) == 0:
                return
            for i in range(0, len(audio), chunk_size):
                yield audio[i:i+chunk_size], i + chunk_size >= len(audio)

    def get_sample_rate(self) -> int:
        return self._sample_rate
