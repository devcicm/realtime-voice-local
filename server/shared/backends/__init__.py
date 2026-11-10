"""
TTSFactory - Selecciona el backend apropiado segun hardware y configuracion.

Logica:
  1. Si el usuario forza un backend (TTS_BACKEND=xtts), usar ese.
  2. Si no, autodetectar:
     - GPU con >=4GB VRAM + usuario quiere maxima calidad -> XTTS
     - CPU moderna (>=6ta gen) + RAM >= 2GB -> Kokoro (default, ya instalado)
     - CPU vieja (2-5ta gen) + RAM >= 1GB -> Piper (mejor para hardware modesto)
     - Sin nada viable -> Error claro

Uso:
    from src.utils.backends import TTSFactory
    backend = TTSFactory.create("auto")   # autodetecta
    backend.initialize(voice_name="es_ES/miro-high")
    audio, _ = backend.generate("Hola mundo")
"""

import platform
import subprocess
import os
from pathlib import Path
from typing import Optional, Dict, Type, List

from .base import TTSBackend, BackendCapabilities
from .kokoro_backend import KokoroBackend
from .piper_backend import PiperBackend


# Registro de backends disponibles
_REGISTRY: Dict[str, Type[TTSBackend]] = {
    "kokoro": KokoroBackend,
    "piper": PiperBackend,
}


def _register_xtts():
    try:
        from .xtts_backend import XTTSBackend
        _REGISTRY["xtts"] = XTTSBackend
    except ImportError:
        pass


_register_xtts()


class HardwareInfo:
    """Informacion del hardware disponible."""

    def __init__(self):
        self.cpu_brand = ""
        self.cpu_gen = 0
        self.cpu_cores = 0
        self.ram_gb = 0.0
        self.gpu_available = False
        self.gpu_vram_gb = 0.0
        self.gpu_name = ""
        self.os = ""
        self.python_version = ""

    def __repr__(self):
        return (
            f"CPU={self.cpu_brand} gen~{self.cpu_gen} ({self.cpu_cores} cores) | "
            f"RAM={self.ram_gb:.1f}GB | "
            f"GPU={self.gpu_name or 'none'} ({self.gpu_vram_gb:.1f}GB VRAM) | "
            f"OS={self.os}"
        )


def _detect_intel_generation(brand: str) -> int:
    """Estima la generacion de un CPU Intel por el brand string."""
    brand_lower = brand.lower()
    gens = {
        # Sandy Bridge (2nd gen)
        "i3-2": 2, "i5-2": 2, "i7-2": 2, "pentium g6": 2, "celeron g4": 2,
        # Ivy Bridge (3rd gen)
        "i3-3": 3, "i5-3": 3, "i7-3": 3, "pentium g2": 3,
        # Haswell (4th gen)
        "i3-4": 4, "i5-4": 4, "i7-4": 4,
        # Broadwell (5th gen)
        "i3-5": 5, "i5-5": 5, "i7-5": 5,
        # Skylake (6th gen)
        "i3-6": 6, "i5-6": 6, "i7-6": 6,
        # Kaby Lake (7th gen)
        "i3-7": 7, "i5-7": 7, "i7-7": 7,
        # Coffee Lake (8-9th gen)
        "i3-8": 8, "i5-8": 8, "i7-8": 8,
        "i3-9": 9, "i5-9": 9, "i7-9": 9,
        # Comet/Ice Lake (10-11th gen)
        "i3-10": 10, "i5-10": 10, "i7-10": 10,
        "i3-11": 11, "i5-11": 11, "i7-11": 11,
        # Alder Lake (12-13-14th gen)
        "i3-12": 12, "i5-12": 12, "i7-12": 12,
        "i3-13": 13, "i5-13": 13, "i7-13": 13,
        "i3-14": 14, "i5-14": 14, "i7-14": 14,
        # AMD Ryzen
        "ryzen 1": 1, "ryzen 2": 2, "ryzen 3": 3, "ryzen 5": 5,
        "ryzen 7": 7, "ryzen 9": 9,
    }
    for pattern, gen in gens.items():
        if pattern in brand_lower:
            return gen
    return 0  # desconocido


def detect_hardware() -> HardwareInfo:
    """Detecta CPU, RAM, GPU del sistema actual."""
    info = HardwareInfo()
    info.os = platform.system()
    info.python_version = platform.python_version()
    info.cpu_cores = os.cpu_count() or 1

    # CPU brand (multiplataforma)
    try:
        if info.os == "Windows":
            out = subprocess.check_output(
                ["wmic", "cpu", "get", "name"], text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                line = line.strip()
                if line and line.lower() != "name":
                    info.cpu_brand = line
                    break
        elif info.os == "Linux":
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        info.cpu_brand = line.split(":", 1)[1].strip()
                        break
        elif info.os == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            )
            info.cpu_brand = out.strip()
    except Exception:
        info.cpu_brand = platform.processor() or "Unknown"

    info.cpu_gen = _detect_intel_generation(info.cpu_brand)

    # RAM (via psutil si esta disponible, si no fallback OS-specific)
    try:
        import psutil
        info.ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        # Fallback rough estimate
        info.ram_gb = 8.0  # Asumir 8GB si no podemos detectar

    # GPU via torch
    try:
        import torch
        if torch.cuda.is_available():
            info.gpu_available = True
            info.gpu_name = torch.cuda.get_device_name(0)
            info.gpu_vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
    except ImportError:
        pass

    return info


def select_backend(hardware: HardwareInfo, prefer_quality=False) -> str:
    """
    Selecciona el mejor backend segun hardware.
    Returns: nombre del backend ('kokoro', 'piper', 'xtts')
    """
    if hardware.gpu_available and hardware.gpu_vram_gb >= 4.0 and prefer_quality:
        return "xtts"
    # Si no hay GPU o no se quiere, evaluar CPU
    if hardware.ram_gb < 1.0:
        raise RuntimeError(
            f"RAM insuficiente: {hardware.ram_gb:.1f}GB. Minimo 1GB para TTS."
        )
    # En CPU: comparar Kokoro vs Piper
    if hardware.cpu_gen >= 4 and hardware.ram_gb >= 2.0:
        # CPU moderna: Kokoro (mejor calidad) o Piper (mejor castellano)
        return "kokoro"
    elif hardware.cpu_gen >= 2 and hardware.ram_gb >= 1.0:
        # CPU vieja: Piper (mas liviano)
        return "piper"
    else:
        # No identificado, default seguro
        return "piper"


def get_compatibility_matrix() -> List[Dict]:
    """Devuelve la matriz de compatibilidad de los 3 backends."""
    return [
        {
            "backend": "piper",
            "min_ram_gb": 1.0,
            "min_cpu_gen": 2,
            "needs_gpu": False,
            "quality": "Buena",
            "castellano": "NATIVO (es_ES)",
            "latino": "NATIVO (es_MX)",
            "english": "Excelente",
            "voice_cloning": False,
            "install_size_mb": 80,
            "model_size_mb": 63,
            "license": "MIT (comercial OK)",
            "caso_uso": "PCs viejas, Raspberry Pi, servidores low-end, audiolibros largos",
        },
        {
            "backend": "kokoro",
            "min_ram_gb": 2.0,
            "min_cpu_gen": 4,
            "needs_gpu": False,
            "quality": "Muy buena",
            "castellano": "Solo LATINO (3 voces)",
            "latino": "Si (nativo)",
            "english": "Excelente (default)",
            "voice_cloning": False,
            "install_size_mb": 350,
            "model_size_mb": 310,
            "license": "Apache-2.0 (comercial OK)",
            "caso_uso": "PCs modernas CPU, multilingue, streaming de baja latencia",
        },
        {
            "backend": "xtts",
            "min_ram_gb": 8.0,
            "min_cpu_gen": 6,
            "needs_gpu": True,
            "gpu_vram_gb": 4.0,
            "quality": "Excelente",
            "castellano": "NATIVO + voice cloning",
            "latino": "Si + voice cloning",
            "english": "Excelente + cloning",
            "voice_cloning": True,
            "install_size_mb": 2000,
            "model_size_mb": 1800,
            "license": "CPML (NO comercial)",
            "caso_uso": "PCs con GPU, voice cloning, maxima calidad",
        },
    ]


class TTSFactory:
    """Factory para crear backends TTS."""

    @staticmethod
    def list_available() -> List[str]:
        """Lista de backends que se pueden instanciar (segun deps)."""
        available = []
        for name in _REGISTRY:
            if name == "kokoro":
                available.append(name)  # siempre disponible
            elif name == "piper":
                try:
                    import piper
                    available.append(name)
                except ImportError:
                    # Verificar CLI
                    import shutil
                    if shutil.which("piper"):
                        available.append(name)
            elif name == "xtts":
                try:
                    import TTS
                    available.append(name)
                except ImportError:
                    pass
        return available

    @staticmethod
    def create(backend_name: str = "auto", **kwargs) -> TTSBackend:
        """
        Crea un backend TTS.
        Args:
            backend_name: 'auto', 'kokoro', 'piper', 'xtts'
            **kwargs: argumentos especificos del backend
        Returns:
            Instancia de TTSBackend
        """
        if backend_name == "auto":
            hw = detect_hardware()
            print(f"[TTSFactory] Hardware: {hw}")
            selected = select_backend(hw, prefer_quality=kwargs.get("prefer_quality", False))
            print(f"[TTSFactory] Backend seleccionado: {selected}")
            backend_name = selected

        if backend_name not in _REGISTRY:
            raise ValueError(
                f"Backend '{backend_name}' no existe. Disponibles: {list(_REGISTRY.keys())}"
            )

        backend_cls = _REGISTRY[backend_name]
        return backend_cls(**kwargs)

    @staticmethod
    def get_capabilities(backend_name: str) -> Optional[BackendCapabilities]:
        if backend_name not in _REGISTRY:
            return None
        # Crear instancia temporal sin inicializar
        try:
            instance = _REGISTRY[backend_name]()
            return instance.capabilities
        except Exception:
            return None


__all__ = [
    "TTSBackend",
    "BackendCapabilities",
    "VoiceInfo",
    "KokoroBackend",
    "PiperBackend",
    "XTTSBackend",
    "TTSFactory",
    "detect_hardware",
    "select_backend",
    "get_compatibility_matrix",
    "HardwareInfo",
]
