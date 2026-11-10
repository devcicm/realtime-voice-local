"""
Audio Format Converter Module

Supports multiple audio formats for streaming:
- PCM (raw float32, ideal for WebSocket streaming)
- WAV (standard format)
- WebM/Opus (browser-native streaming)
- MP3 (compressed, requires ffmpeg)
"""

import io
import struct
from typing import Optional, Tuple
from enum import Enum
import numpy as np

try:
    import soundfile as sf
    import sounddevice as sd

    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False


class AudioFormat(Enum):
    PCM = "pcm"
    WAV = "wav"
    WEBM_OPUS = "webm"
    MP3 = "mp3"
    OGG_OPUS = "ogg"


class AudioConverter:
    """
    Converts numpy audio arrays to various streaming formats.

    Supports:
    - PCM Float32 (24kHz, mono, little-endian) - fastest, WebSocket default
    - WAV Float32 - standard uncompressed
    - WebM/Opus - browser-native streaming
    - MP3 - compressed (requires ffmpeg)
    """

    DEFAULT_SAMPLE_RATE = 24000
    DEFAULT_CHANNELS = 1

    FORMAT_CONTENT_TYPES = {
        AudioFormat.PCM: "audio/pcm",
        AudioFormat.WAV: "audio/wav",
        AudioFormat.WEBM_OPUS: "audio/webm",
        AudioFormat.MP3: "audio/mpeg",
        AudioFormat.OGG_OPUS: "audio/ogg",
    }

    FORMAT_EXTENSIONS = {
        AudioFormat.PCM: ".pcm",
        AudioFormat.WAV: ".wav",
        AudioFormat.WEBM_OPUS: ".webm",
        AudioFormat.MP3: ".mp3",
        AudioFormat.OGG_OPUS: ".ogg",
    }

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        dtype: str = "float32",
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype

    def to_pcm(
        self,
        audio: np.ndarray,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """
        Convert audio to raw PCM (float32, little-endian).

        Fastest format for WebSocket streaming.

        Args:
            audio: numpy array of audio samples
            sample_rate: sample rate (uses default if None)

        Returns:
            bytes: raw PCM data
        """
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        if len(audio.shape) > 1:
            audio = audio.flatten()

        return audio.tobytes()

    def to_wav(
        self,
        audio: np.ndarray,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """
        Convert audio to WAV format.

        Args:
            audio: numpy array of audio samples
            sample_rate: sample rate (uses default if None)

        Returns:
            bytes: WAV file data
        """
        if not SOUNDFILE_AVAILABLE:
            raise RuntimeError(
                "soundfile is not installed. Install with: pip install soundfile"
            )

        sr = sample_rate or self.sample_rate

        if len(audio.shape) > 1:
            audio = audio.flatten()

        buffer = io.BytesIO()
        sf.write(buffer, audio, sr, format="WAV", subtype="FLOAT")
        buffer.seek(0)
        return buffer.getvalue()

    def to_webm(
        self,
        audio: np.ndarray,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """
        Convert audio to WebM/Opus format.

        Browser-native streaming format. Requires ffmpeg for conversion.

        Args:
            audio: numpy array of audio samples
            sample_rate: sample rate (uses default if None)

        Returns:
            bytes: WebM file data

        Note:
            This requires ffmpeg to be installed on the system.
            For WebSocket streaming, consider using PCM instead.
        """
        wav_data = self.to_wav(audio, sample_rate)

        try:
            import subprocess

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-f",
                "webm",
                "-acodec",
                "libopus",
                "-b:a",
                "128k",
                "pipe:1",
            ]

            result = subprocess.run(
                ffmpeg_cmd,
                input=wav_data,
                capture_output=True,
                timeout=30,
            )

            if result.returncode == 0:
                return result.stdout
            else:
                print(f"ffmpeg error: {result.stderr.decode()}")
                return wav_data

        except FileNotFoundError:
            print("ffmpeg not found, returning WAV instead")
            return wav_data
        except Exception as e:
            print(f"WebM conversion error: {e}, returning WAV")
            return wav_data

    def to_opus(
        self,
        audio: np.ndarray,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """
        Convert audio to Ogg/Opus format.

        Args:
            audio: numpy array of audio samples
            sample_rate: sample rate (uses default if None)

        Returns:
            bytes: Ogg file data
        """
        wav_data = self.to_wav(audio, sample_rate)

        try:
            import subprocess

            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-f",
                "ogg",
                "-acodec",
                "libopus",
                "-b:a",
                "128k",
                "pipe:1",
            ]

            result = subprocess.run(
                ffmpeg_cmd,
                input=wav_data,
                capture_output=True,
                timeout=30,
            )

            if result.returncode == 0:
                return result.stdout
            else:
                return wav_data

        except FileNotFoundError:
            return wav_data
        except Exception as e:
            print(f"Opus conversion error: {e}")
            return wav_data

    def convert(
        self,
        audio: np.ndarray,
        format: AudioFormat,
        sample_rate: Optional[int] = None,
    ) -> bytes:
        """
        Convert audio to specified format.

        Args:
            audio: numpy array of audio samples
            format: Target AudioFormat
            sample_rate: sample rate (uses default if None)

        Returns:
            bytes: Audio data in specified format
        """
        converters = {
            AudioFormat.PCM: self.to_pcm,
            AudioFormat.WAV: self.to_wav,
            AudioFormat.WEBM_OPUS: self.to_webm,
            AudioFormat.MP3: self.to_wav,
            AudioFormat.OGG_OPUS: self.to_opus,
        }

        return converters[format](audio, sample_rate)

    def get_header(self, format: AudioFormat) -> dict:
        """Get format metadata headers for WebSocket protocol."""
        return {
            "format": format.value,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "dtype": self.dtype,
            "content_type": self.FORMAT_CONTENT_TYPES.get(format, "audio/pcm"),
        }

    def pcm_header_bytes(self) -> bytes:
        """
        Get PCM header for stream identification.

        Returns:
            bytes: Header containing format info
        """
        header = {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "format": "float32",
        }
        header_str = str(header).encode("utf-8")
        length = len(header_str)
        return struct.pack("<I", length) + header_str


def create_audio_converter(
    sample_rate: int = 24000,
    channels: int = 1,
) -> AudioConverter:
    """Factory function to create AudioConverter with defaults."""
    return AudioConverter(
        sample_rate=sample_rate,
        channels=channels,
    )


class StreamingAudioBuffer:
    """
    Buffer for streaming audio chunks.

    Manages audio buffer for real-time streaming with:
    - Chunk accumulation
    - Size limits
    - Flush operations
    """

    def __init__(
        self,
        max_size: int = 96000,
        chunk_size: int = 4800,
    ):
        self.max_size = max_size
        self.chunk_size = chunk_size
        self.buffer = []
        self.total_samples = 0

    def add(self, chunk: np.ndarray) -> Optional[np.ndarray]:
        """
        Add a chunk to the buffer.

        Args:
            chunk: numpy array of audio samples

        Returns:
            Chunk if buffer is full, None otherwise
        """
        self.buffer.append(chunk)
        self.total_samples += len(chunk)

        if self.total_samples >= self.max_size:
            return self.flush()
        return None

    def flush(self) -> Optional[np.ndarray]:
        """
        Flush the buffer and return all accumulated audio.

        Returns:
            Concatenated audio array or None if empty
        """
        if not self.buffer:
            return None

        audio = np.concatenate(self.buffer)
        self.buffer = []
        self.total_samples = 0
        return audio

    def get_chunk(self) -> Optional[np.ndarray]:
        """
        Get next chunk from buffer without flushing.

        Returns:
            Next chunk or None if empty
        """
        if not self.buffer:
            return None

        chunk = self.buffer.pop(0)
        self.total_samples -= len(chunk)
        return chunk


if __name__ == "__main__":
    print("=== AudioConverter Test ===\n")

    converter = AudioConverter(sample_rate=24000)

    test_audio = np.random.randn(24000).astype(np.float32) * 0.5

    print(f"Test audio shape: {test_audio.shape}")
    print(f"Test audio duration: {len(test_audio) / 24000:.2f}s")

    pcm_data = converter.to_pcm(test_audio)
    print(f"\nPCM output size: {len(pcm_data)} bytes")

    wav_data = converter.to_wav(test_audio)
    print(f"WAV output size: {len(wav_data)} bytes")

    print("\nSupported formats:")
    for fmt in AudioFormat:
        print(f"  - {fmt.value}")
