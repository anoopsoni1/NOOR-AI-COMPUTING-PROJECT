"""Central configuration. All values come from environment variables / `.env`."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / "backend" / ".env", ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    # --- LLM (providers: ollama | groq | gemini) ---------------------------------
    llm_provider: str = "ollama"  # ollama | groq | gemini
    llm_api_key: SecretStr = SecretStr("")  # unused by Ollama; generic placeholder
    groq_api_key: SecretStr = SecretStr("")  # Groq cloud key (never sent to the frontend)
    gemini_api_key: SecretStr = SecretStr("")  # Google Gemini key (never sent to the frontend)
    llm_model: str = "llama3.2"
    llm_vision_model: str = ""  # defaults to llm_model
    llm_base_url: str = ""  # empty = provider default
    ollama_host: str = "http://localhost:11434"
    llm_timeout_s: float = 90.0
    vision_enabled: bool = True
    verify_with_llm: bool = False  # extra LLM judge per click/type/open step (accurate but token-hungry; deterministic checks always run)

    # --- Response language (applies to LLM replies and TTS) ----------------------
    response_language: str = "english"  # english | hinglish | hindi

    # --- Voice -------------------------------------------------------------------
    wake_word_enabled: bool = True
    wake_phrase: str = "hey noor"
    stt_provider: str = "auto"  # auto | openai | openai_compatible | local_whisper
    stt_model: str = ""  # hosted model; default whisper-1
    stt_base_url: str = ""
    stt_language: str = "en"
    local_stt_model: str = "tiny.en"  # faster-whisper model for wake word / interruptions (use "tiny" for multilingual)
    stt_interim: bool = True  # live captions from the local model while you speak
    tts_provider: str = "kokoro"  # kokoro | edge | piper | openai | elevenlabs | system | off
    tts_model: str = ""
    tts_voice: str = "af_heart"  # Kokoro default af_heart
    tts_rate: str = "+0%"
    tts_base_url: str = ""
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM" 
    vad_engine: str = "energy"
    vad_speech_start_threshold: float = 0.012  # RMS (0-1) a frame must exceed to start speech
    vad_speech_end_threshold: float = 0.008  # lower bar to keep speech going (hysteresis)
    vad_silence_timeout_ms: int = 900  # silence that ends your utterance
    vad_min_speech_ms: int = 200  # shorter bursts are ignored
    vad_max_utterance_s: float = 20.0
    vad_preroll_ms: int = 400  # audio kept from before speech is detected so the first words are not cut off
    wake_sensitivity: float = 0.75  # 0.55 forgiving ... 0.95 strict
    wake_cooldown_s: float = 1.5  # ignore repeat wake detections this soon after one
    echo_similarity: float = 0.6  # heard text this similar to what NOOR just said is treated as its own echo
    listen_timeout_s: float = 8.0  # after "Hey NOOR" with no command
    conversation_window_s: float = 20.0  # follow-ups need no wake word for this long after NOOR finishes

    # --- MongoDB -----------------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017"
    database_name: str = "noor"
    allow_in_memory_db: bool = True  # fall back to mongomock when MongoDB is unreachable (dev only)

    # --- Networking --------------------------------------------------------------
    backend_url: str = "http://127.0.0.1:8000"
    frontend_url: str = "http://localhost:5173"
    api_token: SecretStr = SecretStr("")  # optional shared secret for the local API

    # --- Agent limits ------------------------------------------------------------
    max_steps: int = 40  # total tool executions per task
    max_step_retries: int = 2
    max_replans: int = 3
    approval_timeout_s: float = 300.0
    user_response_timeout_s: float = 900.0

    # --- Browser / desktop -------------------------------------------------------
    browser_headless: bool = False
    browser_channel: str = "chrome"  # "chrome", "msedge" or "" for bundled Chromium
    desktop_automation_enabled: bool = True

    # --- Storage / retention -----------------------------------------------------
    data_dir: Path = ROOT / "data"
    screenshot_retention_hours: int = 24
    task_retention_days: int = 90
    log_level: str = "INFO"

    # --- Email (optional) --------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.backend_url).hostname or "127.0.0.1"

    @property
    def port(self) -> int:
        return urlparse(self.backend_url).port or 8000

    @property
    def allowed_origins(self) -> list[str]:
        origins = {self.frontend_url.rstrip("/"), "http://localhost:5173", "http://127.0.0.1:5173", "null"}
        return sorted(origins)

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "ollama":
            return True  # local Ollama needs no cloud API key
        key = (self.groq_api_key if self.llm_provider == "groq" else self.gemini_api_key).get_secret_value()
        return bool(key)

    def llm_defaults(self) -> tuple[str, str]:
        """(base_url, model) defaults for the configured provider when the env vars are empty."""
        if self.llm_provider == "groq":
            return self.llm_base_url or "https://api.groq.com/openai/v1", self.llm_model or "llama-3.1-8b-instant"
        if self.llm_provider == "gemini":
            return self.llm_base_url or "https://generativelanguage.googleapis.com/v1beta", self.llm_model or "gemini-1.5-flash"
        return self.llm_base_url or "http://localhost:11434", self.llm_model or "llama3.2"

    def stt_language_for(self, response_language: str | None = None) -> str:
        """Whisper language hint: do not force speech recognition to English only so Hindi, Hinglish, and English speech are all recognized."""
        lang = (response_language or self.response_language).lower()
        if lang == "hindi":
            return "hi"
        return ""  # Auto-detect for English and Hinglish: allows Hindi, Hinglish, and English speech input

    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def browser_profile_dir(self) -> Path:
        return self.data_dir / "browser-profile"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s

