"""User-adjustable preferences layered over environment defaults. Everything here is editable in Settings and applied live."""

from __future__ import annotations



from typing import Any, Literal



from pydantic import BaseModel, Field



from backend.config import Settings





class Preferences(BaseModel):

    # ---- agent

    max_steps: int = Field(40, ge=1, le=200)

    max_step_retries: int = Field(2, ge=0, le=5)

    max_replans: int = Field(3, ge=0, le=8)

    automation_timeout_s: int = Field(600, ge=30, le=7200)  # whole-task wall clock limit

    browser_headless: bool = False

    policy_medium: Literal["ask", "auto", "block"] = "ask"  # move/rename/modify files, run scripts, install software ...

    policy_high: Literal["confirm", "block"] = "confirm"  # email, uploads, financial, security, destructive: never automatic

    # ---- memory / retention

    screenshot_retention_hours: int = Field(24, ge=0, le=24 * 365)

    task_retention_days: int = Field(90, ge=1, le=3650)

    auto_memory: bool = True

    memory_enabled: bool = True  # master switch: off = nothing is recalled or stored

    # ---- voice output

    voice_replies: bool = True

    tts_provider: Literal["kokoro", "edge", "piper", "openai", "elevenlabs", "system", "off"] = "kokoro"

    tts_voice: str = "af_heart"

    speaking_rate: int = Field(0, ge=-50, le=50)  # percent faster/slower than normal

    tts_speed: float = Field(1.0, ge=0.5, le=2.0)

    response_style: Literal["concise", "normal"] = "concise"

    response_language: Literal["english", "hinglish", "hindi"] = "english"

    # ---- listening

    wake_word_enabled: bool = True

    wake_sensitivity: float = Field(0.75, ge=0.55, le=0.95)  # higher = stricter (fewer accidental wakes)

    conversation_window_s: float = Field(20.0, ge=0, le=120)

    speech_start_threshold: float = Field(0.012, ge=0.002, le=0.2)

    speech_end_threshold: float = Field(0.008, ge=0.001, le=0.2)

    silence_timeout_ms: int = Field(900, ge=300, le=3000)

    minimum_speech_ms: int = Field(200, ge=60, le=1000)

    maximum_utterance_s: float = Field(20.0, ge=3, le=60)

    noise_suppression: bool = True  # applied by the browser audio stack when the microphone starts

    echo_cancellation: bool = True

    auto_gain: bool = True

    # ---- llm provider (runtime overrides; empty/None = use env defaults)

    llm_provider: Literal["ollama", "groq", "gemini"] | None = None

    llm_model: str | None = None

    llm_fallback_provider: Literal["ollama", "groq", "gemini", "none"] = "none"

    llm_fallback_enabled: bool = False

    # ---- developer

    dev_metrics: bool = False





def defaults(s: Settings) -> dict[str, Any]:

    return Preferences(

        max_steps=s.max_steps, max_step_retries=s.max_step_retries, max_replans=s.max_replans, browser_headless=s.browser_headless,

        screenshot_retention_hours=s.screenshot_retention_hours, task_retention_days=s.task_retention_days, wake_word_enabled=s.wake_word_enabled,

        conversation_window_s=s.conversation_window_s, tts_provider=s.tts_provider or "kokoro", tts_voice=s.tts_voice or "af_heart",

        speech_start_threshold=s.vad_speech_start_threshold, speech_end_threshold=s.vad_speech_end_threshold,

        silence_timeout_ms=s.vad_silence_timeout_ms, minimum_speech_ms=s.vad_min_speech_ms, maximum_utterance_s=s.vad_max_utterance_s,

        wake_sensitivity=s.wake_sensitivity, response_language=s.response_language,

    ).model_dump()





def effective(s: Settings, stored: dict[str, Any]) -> dict[str, Any]:

    merged = {**defaults(s), **{k: v for k, v in stored.items() if k in Preferences.model_fields}}

    return Preferences(**merged).model_dump()



