"""Central TTS Manager for Noor: orchestrates Kokoro, Piper, Edge, ElevenLabs, and System SAPI engines.

Provides a unified interface for voice synthesis, model caching, hardware selection, and multilingual support.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from backend.config import Settings
from backend.voice.kokoro_service import KokoroService
from backend.voice.piper_service import PiperService
from backend.voice.text_to_speech import (
    EdgeTTS,
    OpenAICompatTTS,
    TTSProvider,
    WindowsSapiTTS,
    compatible_voice,
    speakable,
)
from backend.voice.voice_config import (
    ALL_VOICES,
    EDGE_VOICES,
    ELEVENLABS_VOICES,
    KOKORO_VOICES,
    PIPER_VOICES,
    SYSTEM_VOICES,
    TTSProviderType,
    VoiceProfile,
    resolve_voice_for_language,
)

log = logging.getLogger("noor.voice.manager")


class ElevenLabsTTS:
    """Optional ElevenLabs cloud TTS provider (disabled by default, requires API key)."""
    name = "elevenlabs"

    def __init__(self, api_key: str = "", voice_id: str = "21m00Tcm4TlvDq8ikWAM", model_id: str = "eleven_multilingual_v2") -> None:
        self.api_key = api_key
        self.voice_id = voice_id or "21m00Tcm4TlvDq8ikWAM"
        self.model_id = model_id

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    async def synth(self, text: str) -> tuple[bytes, str]:
        if not self.configured:
            raise RuntimeError("ElevenLabs is not configured. Add ELEVENLABS_API_KEY in .env to use this provider.")
        import httpx
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        body = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                raise RuntimeError(f"ElevenLabs TTS failed ({r.status_code}): {r.text[:200]}")
            return r.content, "audio/mpeg"


class KokoroTTSAdapter:
    """Adapts KokoroService to the standard TTSProvider protocol."""
    name = "kokoro"

    def __init__(self, service: KokoroService, voice: str = "af_heart", speed: float = 1.0, lang: str | None = None) -> None:
        self.service = service
        self.voice = voice or "af_heart"
        self.speed = speed
        self.lang = lang

    async def synth(self, text: str) -> tuple[bytes, str]:
        return await self.service.synth(text, voice=self.voice, speed=self.speed, lang=self.lang)


class PiperTTSAdapter:
    """Adapts PiperService to the standard TTSProvider protocol."""
    name = "piper"

    def __init__(self, service: PiperService, voice: str = "en_US-lessac-medium", speed: float = 1.0) -> None:
        self.service = service
        self.voice = voice or "en_US-lessac-medium"
        self.speed = speed

    async def synth(self, text: str) -> tuple[bytes, str]:
        return await self.service.synth(text, voice=self.voice, speed=self.speed)


class TTSManager:
    """Central orchestrator for all text-to-speech engines in Noor."""

    _instance: TTSManager | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.kokoro = KokoroService.get_instance()
        self.piper = PiperService.get_instance()
        self.elevenlabs = ElevenLabsTTS(
            api_key=settings.elevenlabs_api_key.get_secret_value() if hasattr(settings, "elevenlabs_api_key") else "",
            voice_id=getattr(settings, "elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM"),
        )
        self.active_provider: str = settings.tts_provider or "kokoro"
        self.active_voice: str = settings.tts_voice or "af_heart"
        self.active_speed: float = 1.0
        self.active_language: str = settings.response_language or "english"

        # Concurrency semaphore: prevent multiple simultaneous model runs on 16GB RAM / 4GB VRAM
        self._synth_sem = asyncio.Semaphore(2)

    @classmethod
    def get_instance(cls, settings: Settings | None = None) -> TTSManager:
        if cls._instance is None:
            if settings is None:
                from backend.config import get_settings
                settings = get_settings()
            cls._instance = cls(settings)
        return cls._instance

    def update_config(self, provider: str | None = None, voice: str | None = None, speed: float | None = None, lang: str | None = None) -> None:
        """Update active TTS parameters without altering LLM providers."""
        if provider:
            self.active_provider = provider.lower().strip()
        if voice:
            self.active_voice = voice
        if speed is not None:
            self.active_speed = max(0.5, min(2.0, float(speed)))
        if lang:
            self.active_language = lang.lower().strip()

    def list_providers(self) -> list[dict[str, Any]]:
        """List all available TTS engines with capabilities and hardware status."""
        return [
            {
                "id": "kokoro",
                "name": "Kokoro (Free, Local)",
                "is_local": True,
                "needs_key": False,
                "configured": True,
                "device": self.kokoro.device,
                "sample_rate": 24000,
                "description": "82M parameter lightweight open-weight local neural TTS. Runs on CPU/CUDA.",
                "status": "ready" if self.kokoro.is_ready else "available",
            },
            {
                "id": "edge",
                "name": "Edge TTS (Free, Neural Cloud)",
                "is_local": False,
                "needs_key": False,
                "configured": True,
                "device": "cloud",
                "sample_rate": 24000,
                "description": "High-quality cloud neural voices with excellent Hindi & Hinglish support.",
                "status": "ready",
            },
            {
                "id": "piper",
                "name": "Piper (Free, Local Fallback)",
                "is_local": True,
                "needs_key": False,
                "configured": self.piper.is_available,
                "device": "cpu",
                "sample_rate": 22050,
                "description": "Ultra-fast local neural TTS designed for embedded / offline devices.",
                "status": "ready" if self.piper.is_available else "not_installed",
            },
            {
                "id": "system",
                "name": "Windows System (SAPI)",
                "is_local": True,
                "needs_key": False,
                "configured": True,
                "device": "cpu",
                "sample_rate": 16000,
                "description": "Built-in offline Windows speech synthesizer fallback.",
                "status": "ready",
            },
            {
                "id": "elevenlabs",
                "name": "ElevenLabs (Cloud, Optional)",
                "is_local": False,
                "needs_key": True,
                "configured": self.elevenlabs.configured,
                "device": "cloud",
                "sample_rate": 44100,
                "description": "Premium cloud voice synthesis (disabled by default; requires API key).",
                "status": "configured" if self.elevenlabs.configured else "needs_key",
            },
        ]

    def list_voices(self, provider: str | None = None, lang: str | None = None) -> list[dict[str, Any]]:
        """Return voices filtered by provider and/or response language."""
        prov = (provider or self.active_provider).lower()
        if prov == "kokoro":
            voices = KOKORO_VOICES
        elif prov == "edge":
            voices = EDGE_VOICES
        elif prov == "piper":
            voices = PIPER_VOICES
        elif prov == "elevenlabs":
            voices = ELEVENLABS_VOICES
        elif prov == "system":
            voices = SYSTEM_VOICES
        else:
            voices = ALL_VOICES

        if lang:
            l = lang.lower().strip()
            # If language filter provided, include matching voices or multilingual voices
            matched = [v for v in voices if v.language in (l, "multilingual")]
            if matched:
                return [v.to_dict() for v in matched]

        return [v.to_dict() for v in voices]

    async def synth(
        self,
        text: str,
        provider: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
        lang: str | None = None,
    ) -> tuple[bytes, str]:
        """Synthesize text into speech with automatic fallback.
        
        Returns:
            (audio_bytes, mime_type)
        """
        text = speakable(text)
        if not text:
            raise ValueError("Text contains no speakable characters.")

        prov = (provider or self.active_provider).lower()
        sp = self.active_speed if speed is None else float(speed)
        la = (lang or self.active_language).lower()

        # Resolve compatible voice
        res_voice, note = resolve_voice_for_language(prov, la, voice or self.active_voice)
        if note:
            log.info("Voice selection: %s", note)

        async with self._synth_sem:
            # 1. Primary requested engine
            try:
                if prov == "kokoro":
                    return await asyncio.wait_for(
                        self.kokoro.synth(text, voice=res_voice, speed=sp, lang=la),
                        timeout=25.0,
                    )
                if prov == "edge":
                    edge_inst = EdgeTTS(res_voice, f"{round((sp - 1.0) * 100):+d}%")
                    return await asyncio.wait_for(edge_inst.synth(text), timeout=15.0)
                if prov == "piper":
                    return await asyncio.wait_for(self.piper.synth(text, voice=res_voice, speed=sp), timeout=15.0)
                if prov == "elevenlabs":
                    return await asyncio.wait_for(self.elevenlabs.synth(text), timeout=15.0)
                if prov == "system":
                    sys_inst = WindowsSapiTTS()
                    return await asyncio.wait_for(sys_inst.synth(text), timeout=15.0)
            except Exception as exc:
                log.warning("Primary TTS provider '%s' failed for voice '%s': %s", prov, res_voice, exc)

            # 2. Fallback Chain
            # If Kokoro failed (or if Edge failed): try Edge, then System SAPI
            if prov != "edge":
                try:
                    log.info("Attempting fallback to Edge TTS...")
                    fallback_voice = "hi-IN-MadhurNeural" if la in ("hindi", "hinglish") else "en-US-AriaNeural"
                    edge_inst = EdgeTTS(fallback_voice, "+0%")
                    return await asyncio.wait_for(edge_inst.synth(text), timeout=15.0)
                except Exception as exc2:
                    log.warning("Edge TTS fallback failed: %s", exc2)

            # 3. Ultimate offline Windows SAPI fallback
            try:
                log.info("Attempting offline Windows SAPI fallback...")
                sys_inst = WindowsSapiTTS()
                return await asyncio.wait_for(sys_inst.synth(text), timeout=15.0)
            except Exception as exc3:
                log.error("All TTS providers and fallbacks failed: %s", exc3)
                raise RuntimeError(f"All TTS providers failed. Last error: {exc3}") from exc3

    async def test_voice(
        self,
        provider: str | None = None,
        voice: str | None = None,
        text: str | None = None,
        speed: float = 1.0,
        lang: str | None = None,
    ) -> dict[str, Any]:
        """Test speech generation and measure RTF/latency."""
        prov = provider or self.active_provider
        vc = voice or self.active_voice
        la = lang or self.active_language
        sample_text = text or (
            "नमस्ते, मैं नूर हूँ।" if la == "hindi"
            else "Hello! I am Noor, your personal AI computer operator. Everything is working smoothly."
        )

        t0 = time.time()
        audio_bytes, mime = await self.synth(sample_text, provider=prov, voice=vc, speed=speed, lang=la)
        infer_time = time.time() - t0

        import soundfile as sf
        import io
        try:
            bio = io.BytesIO(audio_bytes)
            info = sf.info(bio)
            duration = info.duration
            sample_rate = info.samplerate
        except Exception:
            duration = max(0.5, len(audio_bytes) / 48000)
            sample_rate = 24000

        rtf = infer_time / max(duration, 0.001)

        return {
            "success": True,
            "provider": prov,
            "voice": vc,
            "language": la,
            "duration_seconds": round(duration, 2),
            "inference_ms": round(infer_time * 1000, 1),
            "rtf": round(rtf, 3),
            "sample_rate": sample_rate,
            "mime_type": mime,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
        }

    def get_status(self) -> dict[str, Any]:
        """Current TTS operational status."""
        return {
            "provider": self.active_provider,
            "voice": self.active_voice,
            "speed": self.active_speed,
            "language": self.active_language,
            "device": self.kokoro.device if self.active_provider == "kokoro" else "cloud/cpu",
            "model_cached": self.kokoro.is_ready,
            "load_time_seconds": round(self.kokoro.load_time, 2),
            "load_error": self.kokoro.load_error,
            "sample_rate": 24000 if self.active_provider in ("kokoro", "edge") else 16000,
        }

    def build_chain(self) -> list[TTSProvider]:
        """Build provider chain for continuous conversation playback."""
        p = self.active_provider
        if p in ("off", "none", "disabled"):
            return []

        chain: list[TTSProvider] = []

        if p == "kokoro":
            chain.append(KokoroTTSAdapter(self.kokoro, voice=self.active_voice, speed=self.active_speed, lang=self.active_language))
        elif p == "piper":
            chain.append(PiperTTSAdapter(self.piper, voice=self.active_voice, speed=self.active_speed))
        elif p == "edge":
            chain.append(EdgeTTS(self.active_voice, f"{round((self.active_speed - 1.0) * 100):+d}%"))
        elif p == "elevenlabs":
            chain.append(self.elevenlabs)
        elif p == "system":
            chain.append(WindowsSapiTTS())

        # Always include Edge TTS (if not primary) and System SAPI as backup so Noor never loses her voice
        if p != "edge":
            chain.append(EdgeTTS("hi-IN-MadhurNeural" if self.active_language in ("hindi", "hinglish") else "en-US-AriaNeural", "+0%"))
        if not any(isinstance(x, WindowsSapiTTS) for x in chain):
            chain.append(WindowsSapiTTS())

        return chain

