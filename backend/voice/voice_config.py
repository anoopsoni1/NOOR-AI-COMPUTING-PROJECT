"""Voice configurations, provider schemas, and multilingual voice catalogs for Noor."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field


class TTSProviderType(str, Enum):
    KOKORO = "kokoro"
    PIPER = "piper"
    EDGE = "edge"
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    SYSTEM = "system"
    OFF = "off"


class LanguageCode(str, Enum):
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"


class DeviceType(str, Enum):
    AUTO = "auto"
    CUDA = "cuda"
    CPU = "cpu"


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    name: str
    provider: str
    language: str  # english | hindi | hinglish | multilingual
    gender: Literal["female", "male", "neutral"]
    accent: str = ""
    sample_rate: int = 24000
    description: str = ""
    is_default: bool = False
    is_local: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "language": self.language,
            "gender": self.gender,
            "accent": self.accent,
            "sample_rate": self.sample_rate,
            "description": self.description,
            "is_default": self.is_default,
            "is_local": self.is_local,
        }


# ==============================================================================
# OFFICIAL KOKORO 82M v1.0 VOICE CATALOG (Local, 24kHz, 82M parameters)
# ==============================================================================
KOKORO_VOICES: list[VoiceProfile] = [
    # American English (Flagship & Core)
    VoiceProfile("af_heart", "Heart (Warm & Natural)", "kokoro", "english", "female", "en-US", 24000, "Flagship American Female voice", is_default=True),
    VoiceProfile("af_bella", "Bella (Expressive)", "kokoro", "english", "female", "en-US", 24000, "Expressive American Female"),
    VoiceProfile("af_sarah", "Sarah (Clear & Calm)", "kokoro", "english", "female", "en-US", 24000, "Calm, professional American Female"),
    VoiceProfile("af_nicole", "Nicole (Soft)", "kokoro", "english", "female", "en-US", 24000, "Soft American Female"),
    VoiceProfile("af_sky", "Sky (Bright)", "kokoro", "english", "female", "en-US", 24000, "Bright American Female"),
    VoiceProfile("af_alloy", "Alloy (Neutral)", "kokoro", "english", "female", "en-US", 24000, "Balanced neutral Female"),
    VoiceProfile("af_jessica", "Jessica (Conversational)", "kokoro", "english", "female", "en-US", 24000, "Friendly conversational American Female"),
    VoiceProfile("af_nova", "Nova (Energetic)", "kokoro", "english", "female", "en-US", 24000, "Energetic American Female"),
    VoiceProfile("af_river", "River (Relaxed)", "kokoro", "english", "female", "en-US", 24000, "Relaxed American Female"),
    VoiceProfile("am_adam", "Adam (Deep & Authoritative)", "kokoro", "english", "male", "en-US", 24000, "Authoritative American Male"),
    VoiceProfile("am_michael", "Michael (Natural)", "kokoro", "english", "male", "en-US", 24000, "Natural American Male"),
    VoiceProfile("am_fenrir", "Fenrir (Resonant)", "kokoro", "english", "male", "en-US", 24000, "Deep resonant Male"),
    VoiceProfile("am_liam", "Liam (Casual)", "kokoro", "english", "male", "en-US", 24000, "Casual American Male"),
    VoiceProfile("am_echo", "Echo (Direct)", "kokoro", "english", "male", "en-US", 24000, "Direct American Male"),
    VoiceProfile("am_eric", "Eric (Smooth)", "kokoro", "english", "male", "en-US", 24000, "Smooth American Male"),
    VoiceProfile("am_onyx", "Onyx (Rich)", "kokoro", "english", "male", "en-US", 24000, "Rich American Male"),
    VoiceProfile("am_puck", "Puck (Playful)", "kokoro", "english", "male", "en-US", 24000, "Playful American Male"),
    # British English
    VoiceProfile("bf_emma", "Emma (British Female)", "kokoro", "english", "female", "en-GB", 24000, "Refined British Female"),
    VoiceProfile("bf_isabella", "Isabella (British Female)", "kokoro", "english", "female", "en-GB", 24000, "Warm British Female"),
    VoiceProfile("bf_alice", "Alice (British Female)", "kokoro", "english", "female", "en-GB", 24000, "Clear British Female"),
    VoiceProfile("bf_lily", "Lily (British Female)", "kokoro", "english", "female", "en-GB", 24000, "Bright British Female"),
    VoiceProfile("bm_george", "George (British Male)", "kokoro", "english", "male", "en-GB", 24000, "Distinguished British Male"),
    VoiceProfile("bm_daniel", "Daniel (British Male)", "kokoro", "english", "male", "en-GB", 24000, "Articulate British Male"),
    VoiceProfile("bm_fable", "Fable (British Male)", "kokoro", "english", "male", "en-GB", 24000, "Narrative British Male"),
    VoiceProfile("bm_lewis", "Lewis (British Male)", "kokoro", "english", "male", "en-GB", 24000, "Friendly British Male"),
    # Hindi Voices (v1.0 Kokoro experimental phonetics)
    VoiceProfile("hf_alpha", "Alpha (Hindi Female)", "kokoro", "hindi", "female", "hi", 24000, "Kokoro Hindi Female (Experimental)"),
    VoiceProfile("hf_beta", "Beta (Hindi Female)", "kokoro", "hindi", "female", "hi", 24000, "Kokoro Hindi Female (Experimental)"),
    VoiceProfile("hm_omega", "Omega (Hindi Male)", "kokoro", "hindi", "male", "hi", 24000, "Kokoro Hindi Male (Experimental)"),
    VoiceProfile("hm_psi", "Psi (Hindi Male)", "kokoro", "hindi", "male", "hi", 24000, "Kokoro Hindi Male (Experimental)"),
]

# ==============================================================================
# EDGE-TTS NEURAL VOICE CATALOG (Cloud, Free, High Quality Hindi & Hinglish)
# ==============================================================================
EDGE_VOICES: list[VoiceProfile] = [
    # English
    VoiceProfile("en-US-AriaNeural", "Aria (US Female)", "edge", "english", "female", "en-US", 24000, "Microsoft Neural US English Female", is_local=False),
    VoiceProfile("en-US-GuyNeural", "Guy (US Male)", "edge", "english", "male", "en-US", 24000, "Microsoft Neural US English Male", is_local=False),
    VoiceProfile("en-IN-NeerjaNeural", "Neerja (Indian English)", "edge", "english", "female", "en-IN", 24000, "Indian English Female (Great for tech/Hinglish)", is_local=False),
    VoiceProfile("en-IN-PrabhatNeural", "Prabhat (Indian English)", "edge", "english", "male", "en-IN", 24000, "Indian English Male", is_local=False),
    # Hindi & Hinglish (Native Devanagari & Latin Code-Switching)
    VoiceProfile("hi-IN-MadhurNeural", "Madhur (Hindi Male)", "edge", "hindi", "male", "hi-IN", 24000, "Fluent Hindi & Hinglish Male", is_default=True, is_local=False),
    VoiceProfile("hi-IN-SwaraNeural", "Swara (Hindi Female)", "edge", "hindi", "female", "hi-IN", 24000, "Fluent Hindi & Hinglish Female", is_local=False),
]

# ==============================================================================
# PIPER LOCAL VOICES (Offline fast fallback)
# ==============================================================================
PIPER_VOICES: list[VoiceProfile] = [
    VoiceProfile("en_US-lessac-medium", "Lessac (US Female)", "piper", "english", "female", "en-US", 22050, "Piper Fast Onnx Female", is_local=True),
    VoiceProfile("en_US-libritts-high", "LibriTTS (US Female)", "piper", "english", "female", "en-US", 22050, "Piper High Quality Female", is_local=True),
    VoiceProfile("hi_IN-hindi-medium", "Hindi Piper (Local)", "piper", "hindi", "female", "hi-IN", 22050, "Piper Hindi Model", is_local=True),
]

# ==============================================================================
# ELEVENLABS VOICES (Optional Future Provider)
# ==============================================================================
ELEVENLABS_VOICES: list[VoiceProfile] = [
    VoiceProfile("21m00Tcm4TlvDq8ikWAM", "Rachel (Calm)", "elevenlabs", "multilingual", "female", "en-US", 44100, "ElevenLabs Rachel", is_local=False),
    VoiceProfile("AZnzlk1XvdvUeBnXmlld", "Domi (Strong)", "elevenlabs", "multilingual", "female", "en-US", 44100, "ElevenLabs Domi", is_local=False),
    VoiceProfile("EXAVITQu4vr4xnSDxMaL", "Bella (Expressive)", "elevenlabs", "multilingual", "female", "en-US", 44100, "ElevenLabs Bella", is_local=False),
]

# ==============================================================================
# SYSTEM SAPI VOICES (Offline Windows Fallback)
# ==============================================================================
SYSTEM_VOICES: list[VoiceProfile] = [
    VoiceProfile("system_default", "Windows Default SAPI", "system", "multilingual", "neutral", "", 16000, "Built-in Windows SAPI Speech Synthesizer", is_local=True),
]

ALL_VOICES: list[VoiceProfile] = [
    *KOKORO_VOICES,
    *EDGE_VOICES,
    *PIPER_VOICES,
    *ELEVENLABS_VOICES,
    *SYSTEM_VOICES,
]


# ==============================================================================
# API REQUEST / RESPONSE SCHEMAS
# ==============================================================================
class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000, description="Text to synthesize into speech")
    provider: TTSProviderType | None = Field(default=None, description="Optional provider override")
    voice: str | None = Field(default=None, description="Optional voice ID")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Speech playback rate (0.5x to 2.0x)")
    language: str | None = Field(default=None, description="Response language: english | hindi | hinglish")
    device: DeviceType = Field(default=DeviceType.AUTO, description="Inference device: auto | cuda | cpu")


class TTSResponse(BaseModel):
    success: bool
    audio_base64: str | None = None
    mime_type: str = "audio/wav"
    sample_rate: int = 24000
    duration_seconds: float = 0.0
    inference_ms: float = 0.0
    rtf: float = 0.0  # Real-Time Factor (inference_ms / duration_ms)
    provider: str
    voice: str
    language: str
    error: str | None = None


class VoiceStatusInfo(BaseModel):
    provider: str
    active: bool
    voice: str
    language: str
    device: str
    model_cached: bool
    sample_rate: int
    kokoro_available: bool
    piper_available: bool
    edge_available: bool
    system_available: bool


def resolve_voice_for_language(provider: str, lang: str, requested_voice: str | None) -> tuple[str, str]:
    """Resolve a compatible voice and provider for the requested language.
    
    Returns:
        (resolved_voice, notes_or_warning)
    """
    lang = (lang or "english").lower().strip()
    provider = (provider or "kokoro").lower().strip()
    
    if lang == "hindi":
        if provider == "kokoro":
            # Check if requested voice is one of the Kokoro Hindi voices
            if requested_voice in ("hf_alpha", "hf_beta", "hm_omega", "hm_psi"):
                return requested_voice, "Using Kokoro Hindi voice."
            # Fallback to recommended Kokoro Hindi voice
            return "hf_alpha", "Switched to Kokoro Hindi voice 'hf_alpha'."
        if provider == "edge":
            if requested_voice and (requested_voice.startswith("hi-") or "hindi" in requested_voice.lower()):
                return requested_voice, "Using Edge Hindi voice."
            return "hi-IN-MadhurNeural", "Using Edge Hindi voice 'hi-IN-MadhurNeural'."

    elif lang == "hinglish":
        # Hinglish requires fluent code-switching between Hindi and English vocabulary.
        # Kokoro v1.0 handles pure English and Hindi separately but lacks code-switching.
        # Edge TTS's hi-IN-MadhurNeural / hi-IN-SwaraNeural or en-IN-NeerjaNeural excels at Hinglish.
        if provider == "kokoro":
            # If Kokoro requested for Hinglish, use a voice that handles technical terms best, or note the limitation
            if requested_voice and requested_voice.startswith("hf_"):
                return requested_voice, "Note: Kokoro has experimental Hindi phonetics; Edge TTS is recommended for mixed Hinglish."
            return "af_heart", "Note: Kokoro does not natively code-switch Hinglish. Pronouncing with 'af_heart'."
        if provider == "edge":
            if requested_voice and (requested_voice.startswith("hi-") or requested_voice.startswith("en-IN")):
                return requested_voice, "Using Edge Hinglish voice."
            return "hi-IN-MadhurNeural", "Using Edge Hinglish voice 'hi-IN-MadhurNeural'."

    else:  # English
        if provider == "kokoro":
            if requested_voice and any(v.id == requested_voice and v.language == "english" for v in KOKORO_VOICES):
                return requested_voice, "Using Kokoro English voice."
            return "af_heart", "Using default Kokoro voice 'af_heart'."
        if provider == "edge":
            if requested_voice and (requested_voice.startswith("en-") and not requested_voice.startswith("hi-")):
                return requested_voice, "Using Edge English voice."
            return "en-US-AriaNeural", "Using default Edge English voice 'en-US-AriaNeural'."

    return requested_voice or "af_heart", ""

