"""Kokoro TTS Service: Local, high-quality, lightweight 82M open-weight Text-to-Speech.

Runs via ONNX Runtime (CPU or NVIDIA CUDA), loads models into memory once, and synthesizes 24kHz audio.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import soundfile as sf

from backend.voice.voice_config import (
    DeviceType,
    KOKORO_VOICES,
    VoiceProfile,
)

log = logging.getLogger("noor.voice.kokoro")

# Default model URLs
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin"

# Language code mapping for Kokoro's phonetic pipeline
VOICE_LANG_MAP: dict[str, str] = {
    "a": "en-us",  # American English
    "b": "en-gb",  # British English
    "h": "hi",     # Hindi
    "j": "ja",     # Japanese
    "z": "zh",     # Mandarin Chinese
    "e": "es",     # Spanish
    "f": "fr",     # French
    "i": "it",     # Italian
    "p": "pt-br",  # Brazilian Portuguese
}


class KokoroService:
    """Manages Kokoro model lifecycle, hardware selection, and thread-safe audio synthesis."""

    _instance: KokoroService | None = None
    _synth_lock: asyncio.Lock | None = None
    _load_lock: asyncio.Lock | None = None

    def __init__(self, model_dir: Path | None = None) -> None:
        if model_dir is None:
            # Default to noor/data/models/kokoro
            root = Path(__file__).resolve().parents[2]
            model_dir = root / "data" / "models" / "kokoro"
        self.model_dir = Path(model_dir)
        self.model_path = self.model_dir / "kokoro-v1.0.onnx"
        self.voices_path = self.model_dir / "voices-v1.0.bin"

        self._kokoro: Any | None = None
        self._device: str = "cpu"
        self._load_time: float = 0.0
        self._is_ready: bool = False
        self._load_error: str | None = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._synth_lock is None:
            self._synth_lock = asyncio.Lock()
        return self._synth_lock

    @property
    def load_lock(self) -> asyncio.Lock:
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        return self._load_lock

    @classmethod
    def get_instance(cls, model_dir: Path | None = None) -> KokoroService:
        if cls._instance is None:
            cls._instance = cls(model_dir)
        return cls._instance

    @property
    def is_ready(self) -> bool:
        return self._is_ready and self._kokoro is not None

    @property
    def device(self) -> str:
        return self._device

    @property
    def sample_rate(self) -> int:
        return 24000
        return self._device

    @property
    def load_time(self) -> float:
        return self._load_time

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def detect_available_device(self, preferred: str = "auto") -> tuple[str, list[str]]:
        """Detect available ONNX Runtime execution providers."""
        try:
            import onnxruntime as ort
            providers = ort.get_available_providers()
        except Exception:
            providers = ["CPUExecutionProvider"]

        has_cuda = "CUDAExecutionProvider" in providers
        has_dml = "DmlExecutionProvider" in providers

        pref = (preferred or "auto").lower()
        if pref == "cuda" and has_cuda:
            return "cuda", ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if pref in ("auto", "cuda") and (has_cuda or has_dml):
            provider_list = ["CUDAExecutionProvider", "CPUExecutionProvider"] if has_cuda else ["DmlExecutionProvider", "CPUExecutionProvider"]
            return "cuda" if has_cuda else "directml", provider_list

        return "cpu", ["CPUExecutionProvider"]

    async def ensure_models(self, progress_cb: Callable[[int, int], None] | None = None) -> None:
        """Download model and voice weights if missing."""
        self.model_dir.mkdir(parents=True, exist_ok=True)

        if not self.voices_path.exists():
            log.info("Downloading Kokoro voices-v1.0.bin...")
            await asyncio.to_thread(urllib.request.urlretrieve, VOICES_URL, str(self.voices_path))
            log.info("Downloaded voices-v1.0.bin (%d bytes)", self.voices_path.stat().st_size)

        if not self.model_path.exists():
            log.info("Downloading Kokoro kokoro-v1.0.onnx...")
            await asyncio.to_thread(urllib.request.urlretrieve, MODEL_URL, str(self.model_path))
            log.info("Downloaded kokoro-v1.0.onnx (%d bytes)", self.model_path.stat().st_size)

    async def load_model(self, device: str = "auto") -> None:
        """Load Kokoro model into memory once and cache it."""
        async with self.load_lock:
            if self._kokoro is not None and (device == "auto" or device == self._device):
                return

            try:
                await self.ensure_models()
            except Exception as exc:
                self._load_error = f"Failed to download Kokoro models: {exc}"
                log.error(self._load_error)
                raise RuntimeError(self._load_error) from exc

            t0 = time.time()
            dev_name, ep_list = self.detect_available_device(device)
            self._device = dev_name
            log.info("Loading Kokoro TTS model on %s (%s)...", dev_name, ep_list)

            def _init() -> Any:
                from kokoro_onnx import Kokoro
                import onnxruntime as ort

                sess_options = ort.SessionOptions()
                # Optimize CPU thread allocation on 16GB RAM machines
                sess_options.intra_op_num_threads = min(4, os.cpu_count() or 4)
                sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED

                session = ort.InferenceSession(str(self.model_path), sess_options=sess_options, providers=ep_list)
                return Kokoro.from_session(session, str(self.voices_path))

            try:
                self._kokoro = await asyncio.to_thread(_init)
                self._load_time = time.time() - t0
                self._is_ready = True
                self._load_error = None
                log.info("Kokoro TTS model loaded successfully in %.2fs on %s", self._load_time, self._device)
            except Exception as exc:
                self._is_ready = False
                self._load_error = f"Kokoro load failed: {exc}"
                log.exception("Kokoro model initialization failed")
                raise RuntimeError(self._load_error) from exc

    def _determine_language(self, voice: str, user_lang: str | None = None) -> str:
        """Map voice and requested language to Kokoro's supported language code."""
        if user_lang:
            l = user_lang.lower().strip()
            if l == "hindi":
                return "hi"
            if l == "english":
                return "en-gb" if voice.startswith("b") else "en-us"
            if l == "hinglish":
                # Kokoro does not natively code-switch Hinglish.
                # If Hindi voice used, use hi; if English voice used, use en-us.
                return "hi" if voice.startswith("h") else "en-us"

        prefix = voice[0].lower() if voice else "a"
        return VOICE_LANG_MAP.get(prefix, "en-us")

    async def synth(
        self,
        text: str,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str | None = None,
    ) -> tuple[bytes, str]:
        """Synthesize text into speech. Returns (wav_bytes, 'audio/wav')."""
        if not self.is_ready:
            await self.load_model()

        if self._kokoro is None:
            raise RuntimeError("Kokoro model is not loaded.")

        # Clean speakable text
        from backend.voice.text_to_speech import speakable
        cleaned_text = speakable(text)
        if not cleaned_text:
            raise ValueError("Input text is empty or contains no speakable characters.")

        voice_id = voice or "af_heart"
        lang_code = self._determine_language(voice_id, lang)
        speed = max(0.5, min(2.0, float(speed)))

        t0 = time.time()

        def _infer() -> tuple[bytes, float, int]:
            samples, sample_rate = self._kokoro.create(
                cleaned_text,
                voice=voice_id,
                speed=speed,
                lang=lang_code,
            )
            bio = io.BytesIO()
            sf.write(bio, samples, sample_rate, format="WAV", subtype="PCM_16")
            audio_data = bio.getvalue()
            duration = len(samples) / sample_rate
            return audio_data, duration, sample_rate

        try:
            async with self.lock:
                audio_bytes, duration, sample_rate = await asyncio.to_thread(_infer)
        except Exception as exc:
            log.warning("Kokoro synthesis failed (voice=%s, lang=%s): %s", voice_id, lang_code, exc)
            raise RuntimeError(f"Kokoro synthesis failed: {exc}") from exc

        t_infer = time.time() - t0
        rtf = t_infer / max(duration, 0.001)
        log.debug(
            "Kokoro generated %.2fs audio in %.2fs (RTF: %.2f) [voice=%s, lang=%s]",
            duration,
            t_infer,
            rtf,
            voice_id,
            lang_code,
        )
        return audio_bytes, "audio/wav"

    def get_voices(self) -> list[VoiceProfile]:
        """Return the official Kokoro voice catalog."""
        return list(KOKORO_VOICES)
