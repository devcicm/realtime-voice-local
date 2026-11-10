"""
Voice Downloader Module for Kokoro TTS

Downloads voice packs from HuggingFace repositories automatically.
Supports multiple repositories and concurrent downloads.
"""

import os
import asyncio
from pathlib import Path
from typing import Optional, AsyncGenerator, List, Dict, Callable
from dataclasses import dataclass
import time

try:
    from huggingface_hub import hf_hub_download, list_repo_files, HfApi

    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False


@dataclass
class VoiceInfo:
    name: str
    lang_code: str
    gender: str
    file_path: Optional[Path] = None
    size: Optional[int] = None
    repo_id: Optional[str] = None


class VoiceDownloader:
    """
    Manages voice pack downloads from HuggingFace.

    Supports downloading voices from multiple repositories:
    - hexgrad/Kokoro-82M-v1.1 (English voices)
    - leonelhs/kokoro-thewh1teagle (Multilingual voices)
    """

    REPOSITORIES = {
        "default": "hexgrad/Kokoro-82M-v1.1",
        "multilingual": "leonelhs/kokoro-thewh1teagle",
    }

    LANGUAGE_MAP = {
        "a": "en-us",  # American English
        "b": "en-gb",  # British English
        "e": "es-es",  # Spanish
        "i": "it-it",  # Italian
        "f": "fr-fr",  # French
        "j": "ja-jp",  # Japanese
        "z": "zh-cn",  # Chinese
        "p": "pt-br",  # Portuguese
        "d": "de-de",  # German
        "k": "ko-kr",  # Korean
        "r": "ru-ru",  # Russian
        "n": "nl-nl",  # Dutch
        "h": "hi-in",  # Hindi
    }

    def __init__(
        self,
        voices_dir: Path,
        repo_id: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.voices_dir = Path(voices_dir)
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        self.repo_id = repo_id or self.REPOSITORIES["default"]
        self.token = token
        self._api = None

    @property
    def api(self) -> HfApi:
        if self._api is None and HF_AVAILABLE:
            self._api = HfApi(token=self.token)
        return self._api

    def is_voice_installed(self, voice_name: str) -> bool:
        """Check if a voice is already installed."""
        voice_path = self.voices_dir / f"{voice_name}.pt"
        return voice_path.exists()

    def get_installed_voices(self) -> List[VoiceInfo]:
        """Get list of all installed voices."""
        voices = []
        if not self.voices_dir.exists():
            return voices

        for voice_file in self.voices_dir.glob("*.pt"):
            name = voice_file.stem
            lang_code = name[0] if name else "?"
            gender = (
                name.split("_")[1][0]
                if "_" in name and len(name.split("_")) > 1
                else "?"
            )

            voices.append(
                VoiceInfo(
                    name=name,
                    lang_code=lang_code,
                    gender=gender,
                    file_path=voice_file,
                    size=voice_file.stat().st_size if voice_file.exists() else None,
                )
            )

        return sorted(voices, key=lambda v: v.name)

    def get_voice_info(self, voice_name: str) -> VoiceInfo:
        """Get info about a specific voice."""
        voice_path = self.voices_dir / f"{voice_name}.pt"
        lang_code = voice_name[0] if voice_name else "?"
        gender = (
            voice_name.split("_")[1][0]
            if "_" in voice_name and len(voice_name.split("_")) > 1
            else "?"
        )

        return VoiceInfo(
            name=voice_name,
            lang_code=lang_code,
            gender=gender,
            file_path=voice_path if voice_path.exists() else None,
            size=voice_path.stat().st_size if voice_path.exists() else None,
            repo_id=self.repo_id,
        )

    def download_voice(
        self,
        voice_name: str,
        repo_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Path:
        """
        Download a single voice pack from HuggingFace.

        Args:
            voice_name: Name of the voice (e.g., 'es_f', 'af_nicole')
            repo_id: Optional repository ID (defaults to self.repo_id)
            progress_callback: Optional callback(progress, total)

        Returns:
            Path to the downloaded voice file
        """
        if not HF_AVAILABLE:
            raise RuntimeError(
                "huggingface_hub is not installed. Install with: pip install huggingface-hub"
            )

        target_repo = repo_id or self.repo_id
        voice_path = self.voices_dir / f"{voice_name}.pt"

        if voice_path.exists():
            print(f"Voice '{voice_name}' already installed at {voice_path}")
            return voice_path

        print(f"Downloading voice '{voice_name}' from {target_repo}...")

        try:
            local_path = hf_hub_download(
                repo_id=target_repo,
                filename=f"{voice_name}.pt",
                local_dir=str(self.voices_dir),
                local_dir_use_symlinks=False,
                token=self.token,
            )

            downloaded_path = Path(local_path)
            if downloaded_path.suffix == ".pt":
                final_path = self.voices_dir / f"{voice_name}.pt"
                if downloaded_path != final_path:
                    downloaded_path.rename(final_path)
                return final_path

            return downloaded_path

        except Exception as e:
            try_repo = self.REPOSITORIES["multilingual"]
            if target_repo != try_repo:
                print(f"Trying alternative repository: {try_repo}")
                return self.download_voice(voice_name, try_repo, progress_callback)
            raise RuntimeError(f"Failed to download voice '{voice_name}': {e}")

    def list_remote_voices(
        self,
        repo_id: Optional[str] = None,
    ) -> List[Dict]:
        """List all available voices in a remote repository."""
        if not HF_AVAILABLE:
            return []

        target_repo = repo_id or self.repo_id

        try:
            files = list_repo_files(repo_id=target_repo, token=self.token)
            voices = []

            for f in files:
                if f.startswith("voices/") and f.endswith(".pt"):
                    name = Path(f).stem
                    lang_code = name[0] if name else "?"
                    gender = (
                        name.split("_")[1][0]
                        if "_" in name and len(name.split("_")) > 1
                        else "?"
                    )

                    voices.append(
                        {
                            "name": name,
                            "lang_code": lang_code,
                            "gender": gender,
                            "lang_name": self.LANGUAGE_MAP.get(lang_code, "unknown"),
                            "repo_id": target_repo,
                            "file": f,
                        }
                    )

            return sorted(voices, key=lambda v: v["name"])

        except Exception as e:
            print(f"Error listing remote voices from {target_repo}: {e}")
            return []

    def download_all(
        self,
        repos: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
        voice_filter: Optional[Callable[[str], bool]] = None,
    ) -> Dict[str, List[str]]:
        """
        Download all available voices from specified repositories.

        Args:
            repos: List of repository IDs (defaults to all)
            progress_callback: Callback(voice_name, downloaded, total)
            voice_filter: Optional filter function(name) -> bool

        Returns:
            Dict with 'downloaded', 'skipped', 'failed' lists
        """
        if not HF_AVAILABLE:
            return {
                "downloaded": [],
                "skipped": [],
                "failed": ["huggingface_hub not installed"],
            }

        repos = repos or list(self.REPOSITORIES.values())
        result = {"downloaded": [], "skipped": [], "failed": []}

        all_voices = []
        for repo in repos:
            voices = self.list_remote_voices(repo)
            for v in voices:
                v["repo_id"] = repo
                all_voices.append(v)

        total = len(all_voices)
        print(f"Found {total} voices across {len(repos)} repositories")

        for idx, voice in enumerate(all_voices):
            name = voice["name"]

            if voice_filter and not voice_filter(name):
                continue

            if self.is_voice_installed(name):
                result["skipped"].append(name)
                continue

            try:
                self.download_voice(name, voice["repo_id"])
                result["downloaded"].append(name)
                if progress_callback:
                    progress_callback(name, idx + 1, total)
            except Exception as e:
                result["failed"].append(f"{name}: {str(e)}")

        print(f"\nDownload complete:")
        print(f"  Downloaded: {len(result['downloaded'])}")
        print(f"  Skipped (already installed): {len(result['skipped'])}")
        print(f"  Failed: {len(result['failed'])}")

        return result

    async def download_voice_async(
        self,
        voice_name: str,
        repo_id: Optional[str] = None,
    ) -> Path:
        """Async version of download_voice."""
        return await asyncio.to_thread(self.download_voice, voice_name, repo_id)

    def get_language_voices(self, lang_code: str) -> List[VoiceInfo]:
        """Get all installed voices for a specific language code."""
        return [v for v in self.get_installed_voices() if v.lang_code == lang_code]

    def get_spanish_voices(self) -> List[VoiceInfo]:
        """Get all installed Spanish voices."""
        return self.get_language_voices("e")

    def verify_installation(self) -> Dict:
        """Verify all installed voices and report status."""
        voices = self.get_installed_voices()
        report = {
            "total_installed": len(voices),
            "by_language": {},
            "missing_essential": [],
        }

        essential_langs = {"e", "a", "b", "i", "j", "z"}  # es, en-us, en-gb, it, ja, zh

        for lang in essential_langs:
            lang_voices = self.get_language_voices(lang)
            lang_name = self.LANGUAGE_MAP.get(lang, lang)
            report["by_language"][lang_name] = [v.name for v in lang_voices]

            if not lang_voices:
                report["missing_essential"].append(lang_name)

        return report


def create_voice_downloader(
    voices_dir: Optional[Path] = None,
    token: Optional[str] = None,
) -> VoiceDownloader:
    """Factory function to create a VoiceDownloader with common defaults."""
    if voices_dir is None:
        base_dir = Path(__file__).parent.parent.parent
        voices_dir = base_dir / "data" / "voices"

    return VoiceDownloader(
        voices_dir=voices_dir,
        repo_id="hexgrad/Kokoro-82M-v1.1",
        token=token,
    )


if __name__ == "__main__":
    downloader = create_voice_downloader()

    print("=== Voice Downloader CLI ===\n")
    print("1. List installed voices")
    print("2. List available voices (remote)")
    print("3. Download a specific voice")
    print("4. Download all voices")
    print("5. Download Spanish voices only")
    print("6. Verify installation")

    choice = input("\nSelect option (1-6): ").strip()

    if choice == "1":
        voices = downloader.get_installed_voices()
        print(f"\nInstalled voices ({len(voices)}):")
        for v in voices:
            print(f"  {v.name} ({v.lang_code}/{v.gender})")

    elif choice == "2":
        voices = downloader.list_remote_voices()
        print(f"\nAvailable voices ({len(voices)}):")
        for v in voices:
            print(f"  {v['name']} ({v['lang_code']}/{v['gender']}) - {v['repo_id']}")

    elif choice == "3":
        name = input("Enter voice name: ").strip()
        try:
            path = downloader.download_voice(name)
            print(f"Downloaded to: {path}")
        except Exception as e:
            print(f"Error: {e}")

    elif choice == "4":
        print("\nDownloading all voices (this may take a while)...")
        result = downloader.download_all()
        print(f"\nResult: {result}")

    elif choice == "5":
        print("\nDownloading Spanish voices...")
        result = downloader.download_all(
            voice_filter=lambda name: name.startswith("es_")
        )
        print(f"\nResult: {result}")

    elif choice == "6":
        report = downloader.verify_installation()
        print(f"\nInstallation report:")
        print(f"  Total installed: {report['total_installed']}")
        print(f"  By language:")
        for lang, voices in report["by_language"].items():
            print(f"    {lang}: {voices}")
        print(f"  Missing essential: {report['missing_essential']}")
