"""
TTS Backend interface - Contrato común para todos los proveedores TTS.

Define la API mínima que cualquier backend TTS debe implementar para ser
usado por el VoiceGenerator, el WebSocket server y los scripts de chat.

Este patrón Strategy permite:
  - Cambiar de backend sin tocar el código de llamada
  - Aislar dependencias (Kokoro, Piper, XTTS) entre sí
  - Testear cada backend independientemente
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Iterator, Tuple
import numpy as np


@dataclass
class VoiceInfo:
    """Información de una voz disponible."""
    name: str
    language: str           # "es", "en", "fr", etc.
    language_name: str      # "Spanish (Spain)", "English (US)", etc.
    gender: str             # "f", "m", "?"
    quality: str            # "low", "medium", "high"
    description: str = ""
    needs_gpu: bool = False
    needs_reference_audio: bool = False  # Para voice cloning
    sample_rate: int = 24000


@dataclass
class BackendCapabilities:
    """Capacidades declaradas de un backend."""
    name: str
    min_ram_gb: float           # RAM mínima recomendada
    min_cpu_generation: int     # Generacion Intel minima recomendada (1=Sandy Bridge, 10=Ice Lake)
    needs_gpu: bool
    gpu_vram_gb: float          # VRAM minima si necesita GPU
    supports_streaming: bool
    supports_voice_cloning: bool
    supports_blending: bool
    max_audio_length_s: int     # Audio maximo continuo
    realtime_factor_cpu: float  # 1.0 = realtime, 2.0 = 2x realtime, 0.5 = 0.5x realtime
    install_size_mb: float
    model_size_mb: float
    license: str


class TTSBackend(ABC):
    """
    Interfaz abstracta para backends TTS.

    Cualquier implementación concreta (Kokoro, Piper, XTTS) debe heredar
    de esta clase e implementar todos los métodos abstractos.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador del backend: 'kokoro', 'piper', 'xtts'."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Capacidades declaradas del backend."""
        ...

    @abstractmethod
    def initialize(self, **kwargs) -> str:
        """
        Carga el modelo en memoria.
        Returns: mensaje de estado (ej: 'Loaded piper es_ES/miro-high').
        """
        ...

    @abstractmethod
    def is_initialized(self) -> bool:
        """True si el modelo esta cargado y listo."""
        ...

    @abstractmethod
    def list_voices(self) -> List[VoiceInfo]:
        """Lista de voces disponibles en este backend."""
        ...

    @abstractmethod
    def get_voice(self, name: str) -> Optional[VoiceInfo]:
        """Obtiene info de una voz por nombre. None si no existe."""
        ...

    @abstractmethod
    def change_voice(self, name: str, **kwargs) -> str:
        """
        Cambia la voz activa. Algunos backends (Kokoro) requieren recargar
        el voicepack; otros (XTTS) requieren un audio de referencia.
        Returns: mensaje de estado.
        """
        ...

    @abstractmethod
    def current_voice(self) -> Optional[str]:
        """Nombre de la voz activa. None si no hay voz seleccionada."""
        ...

    @abstractmethod
    def generate(
        self,
        text: str,
        lang: Optional[str] = None,
        speed: float = 1.0,
        **kwargs,
    ) -> Tuple[Optional[np.ndarray], List[str]]:
        """
        Genera audio a partir de texto.
        Returns: (audio_array, phonemes) o (None, []) si falla.
        audio_array es float32 mono a 24kHz (o el sample_rate del backend).
        """
        ...

    @abstractmethod
    def generate_streaming(
        self,
        text: str,
        lang: Optional[str] = None,
        speed: float = 1.0,
        chunk_size: int = 4800,
        **kwargs,
    ) -> Iterator[Tuple[np.ndarray, bool]]:
        """
        Genera audio en chunks para streaming.
        Yields: (chunk_audio, is_final_sentence)
        """
        ...

    @abstractmethod
    def get_sample_rate(self) -> int:
        """Sample rate del audio generado."""
        ...

    def cleanup(self) -> None:
        """Libera recursos (opcional). Default: no hace nada."""
        return None

    def __repr__(self) -> str:
        cap = self.capabilities
        return (
            f"<{self.__class__.__name__} "
            f"name={self.name!r} "
            f"initialized={self.is_initialized()} "
            f"voice={self.current_voice()!r}>"
        )
