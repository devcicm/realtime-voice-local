from pathlib import Path
import os
import platform
import re
from dotenv import load_dotenv

load_dotenv()


def init_espeak():
    """Initialize eSpeak environment variables. Must be called before any other imports."""
    system = platform.system()
    if system == "Windows":
        os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = (
            r"C:\Program Files\eSpeak NG\libespeak-ng.dll"
        )
        os.environ["PHONEMIZER_ESPEAK_PATH"] = (
            r"C:\Program Files\eSpeak NG\espeak-ng.exe"
        )


init_espeak()

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional


class Settings(BaseSettings):
    """Settings class to manage application configurations."""

    BASE_DIR: Path = Path(__file__).parent.parent.parent
    MODELS_DIR: Path = BASE_DIR / "data" / "models"
    VOICES_DIR: Path = BASE_DIR / "data" / "voices"
    OUTPUT_DIR: Path = BASE_DIR / "output"
    RECORDINGS_DIR: Path = BASE_DIR / "recordings"

    ESPEAK_LIBRARY_PATH: str = r"C:\Program Files\eSpeak NG\libespeak-ng.dll"
    ESPEAK_PATH: str = r"C:\Program Files\eSpeak NG\espeak-ng.exe"

    # TTS Configuration (SPANISH - VOCES REALES)
    TTS_MODEL: str = Field(default="kokoro.pth")
    VOICE_NAME: str = Field(default="ef_dora")
    SPEED: float = Field(default=1.2)
    LANG_CODE: str = Field(default="e")  # Spanish (e)

    # HuggingFace
    HUGGINGFACE_TOKEN: str = Field(default="")
    KOKORO_VOICES_REPO: str = Field(
        default="hexgrad/Kokoro-82M-v1.1"
    )

    # LLM Configuration
    LM_STUDIO_URL: str = Field(default="http://127.0.0.1:12344/v1")

    @field_validator("LM_STUDIO_URL")
    @classmethod
    def _ipv4_localhost(cls, v: str) -> str:
        # En Windows "localhost" resuelve primero a ::1 y LM Studio solo escucha
        # en IPv4: cada conexion nueva perdia ~2 s esperando el fallback.
        return re.sub(r"//localhost(?=[:/]|$)", "//127.0.0.1", v)

    OLLAMA_URL: str = Field(default="http://localhost:11434/api/chat")
    DEFAULT_SYSTEM_PROMPT: str = Field(
        default="Eres un asistente amigable. Responde en español de forma natural."
    )
    LLM_MODEL: str = Field(default="qwen/qwen3.5-9b")
    NUM_THREADS: int = Field(default=2)
    MAX_TOKENS: int = Field(default=512)
    LLM_TEMPERATURE: float = Field(default=0.7)
    LLM_STREAM: bool = Field(default=False)
    LLM_RETRY_DELAY: float = Field(default=0.5)
    MAX_RETRIES: int = Field(default=3)

    WHISPER_MODEL: str = Field(default="openai/whisper-tiny.en")

    VAD_MODEL: str = Field(default="pyannote/segmentation-3.0")
    VAD_MIN_DURATION_ON: float = Field(default=0.1)
    VAD_MIN_DURATION_OFF: float = Field(default=0.1)

    # Server Configuration
    SERVER_HOST: str = Field(default="0.0.0.0")
    SERVER_PORT: int = Field(default=8990)

    # TTS Backend selector (multi-backend support)
    TTS_BACKEND: str = Field(default="kokoro")
    # Valores validos: "kokoro" | "piper" | "xtts" | "auto"
    # "auto" detecta hardware y selecciona el mejor
    TTS_PREFER_QUALITY: bool = Field(default=False)
    # Si True y hay GPU, prefiere XTTS aunque sea mas pesado

    # Piper-specific config
    PIPER_VOICES_DIR: str = Field(
        default=""
    )  # Si vacio, usa ~/.local/share/piper/voices
    PIPER_USE_CLI: bool = Field(default=False)

    # XTTS-specific config
    XTTS_MODEL: str = Field(
        default="tts_models/multilingual/multi-dataset/xtts_v2"
    )
    XTTS_REFERENCE_AUDIO: str = Field(
        default=""
    )  # 6s audio para voice cloning
    XTTS_USE_GPU: bool = Field(default=True)

    # Audio Configuration
    CHUNK: int = Field(default=1024)
    CHANNELS: int = Field(default=1)
    RATE: int = Field(default=16000)
    OUTPUT_SAMPLE_RATE: int = Field(default=24000)
    RECORD_DURATION: int = Field(default=5)
    SILENCE_THRESHOLD: float = Field(default=0.01)
    INTERRUPTION_THRESHOLD: float = Field(default=0.02)
    MAX_SILENCE_DURATION: int = Field(default=1)
    SPEECH_CHECK_TIMEOUT: float = Field(default=0.1)
    SPEECH_CHECK_THRESHOLD: float = Field(default=0.02)
    ROLLING_BUFFER_TIME: float = Field(default=0.5)
    TARGET_SIZE: int = Field(default=15)
    FIRST_SENTENCE_SIZE: int = Field(default=3)
    PLAYBACK_DELAY: float = Field(default=0.005)

    def setup_directories(self):
        """Create necessary directories if they don't exist"""
        self.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.VOICES_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


def configure_logging():
    """Configure logging to suppress all logs"""
    import logging
    import warnings

    warnings.filterwarnings("ignore")

    logging.getLogger().setLevel(logging.ERROR)

    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("PIL").setLevel(logging.ERROR)
    logging.getLogger("matplotlib").setLevel(logging.ERROR)
    logging.getLogger("torch").setLevel(logging.ERROR)
    logging.getLogger("tensorflow").setLevel(logging.ERROR)
    logging.getLogger("whisper").setLevel(logging.ERROR)
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("pyannote").setLevel(logging.ERROR)
    logging.getLogger("sounddevice").setLevel(logging.ERROR)
    logging.getLogger("soundfile").setLevel(logging.ERROR)
    logging.getLogger("uvicorn").setLevel(logging.ERROR)
    logging.getLogger("fastapi").setLevel(logging.ERROR)


configure_logging()
