"""Piper TTS Service: Fast, local neural text-to-speech fallback engine."""
from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from backend.voice.voice_config import PIPER_VOICES, VoiceProfile

log = logging.getLogger("noor.voice.piper")


class PiperService:
    """Manages Piper local neural TTS engine and model execution."""

    _instance: PiperService | None = None

    def __init__(self, model_dir: Path | None = None) -> None:
        if model_dir is None:
            root = Path(__file__).resolve().parents[2]
            model_dir = root / "data" / "models" / "piper"
        self.model_dir = Path(model_dir)
        self._piper_bin = shutil.which("piper") or shutil.which("piper.exe")

    @classmethod
    def get_instance(cls, model_dir: Path | None = None) -> PiperService:
        if cls._instance is None:
            cls._instance = cls(model_dir)
        return cls._instance

    @property
    def is_available(self) -> bool:
        """Piper is available if the binary is in PATH or python piper module exists."""
        if self._piper_bin:
            return True
        try:
            import piper  # noqa: F401
            return True
        except ImportError:
            return False

    async def synth(
        self,
        text: str,
        voice: str = "en_US-lessac-medium",
        speed: float = 1.0,
    ) -> tuple[bytes, str]:
        """Synthesize speech using local Piper."""
        if not self.is_available:
            raise RuntimeError(
                "Piper is not installed. To use Piper as a local fallback, install piper-tts or place the piper executable in PATH."
            )

        model_file = self.model_dir / f"{voice}.onnx"
        if not model_file.exists():
            raise FileNotFoundError(
                f"Piper voice model '{voice}.onnx' not found in {self.model_dir}. Download it from https://github.com/rhasspy/piper/releases."
            )

        cmd = [
            self._piper_bin or "piper",
            "--model", str(model_file),
            "--output_raw",
        ]
        if speed != 1.0:
            cmd.extend(["--length_scale", str(round(1.0 / speed, 2))])

        def _run() -> bytes:
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            raw_pcm, err = p.communicate(input=text.encode("utf-8"))
            if p.returncode != 0:
                raise RuntimeError(f"Piper error: {err.decode(errors='replace')}")
            
            # Wrap raw PCM16 in standard WAV container
            import wave
            bio = io.BytesIO()
            with wave.open(bio, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(raw_pcm)
            return bio.getvalue()

        wav_bytes = await asyncio.to_thread(_run)
        return wav_bytes, "audio/wav"

    def get_voices(self) -> list[VoiceProfile]:
        return list(PIPER_VOICES)

