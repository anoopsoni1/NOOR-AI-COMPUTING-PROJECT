"""Comprehensive tests for LLM Switching, Language Selection & Enforcement, and TTS Voice Compatibility."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from backend.config import Settings
from backend.conversation.manager import ConversationManager, persona
from backend.llm import LLMClient
from backend.llm.providers import (
    LLMAuthenticationError,
    LLMError,
    LLMNotFoundError,
    LLMRateLimitError,
    LLMUnavailableError,
    OllamaProvider,
    GroqProvider,
    GeminiProvider,
    PROVIDERS,
    build_provider,
    _normalize_http_error,
)
from backend.voice.text_to_speech import compatible_voice


# ---------------------------------------------------------------------------
# 1. Supported Providers and Rejection of Removed Providers
# ---------------------------------------------------------------------------
def test_only_supported_providers_registered() -> None:
    assert set(PROVIDERS.keys()) == {"ollama", "groq", "gemini"}
    assert "openai" not in PROVIDERS
    assert "anthropic" not in PROVIDERS


def test_build_provider_rejects_removed_providers() -> None:
    s = Settings(_env_file=None, llm_provider="ollama")
    with pytest.raises(LLMError) as exc_info:
        build_provider(s, "openai")
    assert "Unsupported LLM provider 'openai'" in str(exc_info.value)

    with pytest.raises(LLMError) as exc_info2:
        build_provider(s, "anthropic")
    assert "Unsupported LLM provider 'anthropic'" in str(exc_info2.value)


# ---------------------------------------------------------------------------
# 2. Provider Model Listing and Filtering
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ollama_list_models() -> None:
    provider = OllamaProvider("http://localhost:11434", "")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "models": [{"name": "llama3.2:latest"}, {"name": "mistral:7b"}]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = mock_resp

    models = await provider.list_models(mock_http)
    assert len(models) == 2
    assert models[0].id == "llama3.2:latest"
    assert models[1].id == "mistral:7b"


@pytest.mark.asyncio
async def test_groq_list_models_filters_non_text() -> None:
    provider = GroqProvider("https://api.groq.com/openai/v1", "gsk_test")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "data": [
            {"id": "llama-3.1-8b-instant"},
            {"id": "whisper-large-v3"},       # should be filtered
            {"id": "mixtral-8x7b-32768"},
            {"id": "distil-whisper-en"},       # should be filtered
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = mock_resp

    models = await provider.list_models(mock_http)
    ids = [m.id for m in models]
    assert "llama-3.1-8b-instant" in ids
    assert "mixtral-8x7b-32768" in ids
    assert "whisper-large-v3" not in ids
    assert "distil-whisper-en" not in ids


@pytest.mark.asyncio
async def test_gemini_list_models_filters_methods_and_non_text() -> None:
    provider = GeminiProvider("https://generativelanguage.googleapis.com/v1beta", "AIzaTest")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "models": [
            {
                "name": "models/gemini-3.5-flash",
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            },
            {
                "name": "models/text-embedding-004",
                "supportedGenerationMethods": ["embedContent"],  # not generateContent
            },
            {
                "name": "models/imagen-3.0",
                "supportedGenerationMethods": ["generateContent"],  # non-text regex
            },
            {
                "name": "models/gemini-3.6-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-1.5-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.get.return_value = mock_resp

    models = await provider.list_models(mock_http)
    ids = [m.id for m in models]
    assert "gemini-3.5-flash" in ids
    assert "gemini-3.6-flash" in ids
    assert "gemini-1.5-flash" not in ids
    assert "text-embedding-004" not in ids
    assert "imagen-3.0" not in ids


# ---------------------------------------------------------------------------
# 3. Error Normalization and Security (No API Keys Leaked)
# ---------------------------------------------------------------------------
def test_error_normalization_security() -> None:
    resp_401 = MagicMock(status_code=401, text="Invalid auth token: gsk_secret12345")
    err_401 = _normalize_http_error(resp_401, "llama-3.1", "Groq")
    assert isinstance(err_401, LLMAuthenticationError)
    assert "gsk_secret12345" not in str(err_401)
    assert "keys are never shown in the UI" in str(err_401)

    resp_429 = MagicMock(status_code=429, text="Rate limit exceeded")
    err_429 = _normalize_http_error(resp_429, "gemini-1.5-flash", "Gemini")
    assert isinstance(err_429, LLMRateLimitError)
    assert "rate limited" in str(err_429)

    resp_404 = MagicMock(status_code=404, text="model not found")
    err_404 = _normalize_http_error(resp_404, "non-existent-model", "Ollama")
    assert isinstance(err_404, LLMNotFoundError)
    assert "non-existent-model" in str(err_404)

    resp_503 = MagicMock(status_code=503, text="Service Unavailable")
    err_503 = _normalize_http_error(resp_503, "llama-3.1", "Groq")
    assert isinstance(err_503, LLMUnavailableError)


# ---------------------------------------------------------------------------
# 4. Live Provider and Model Switching Without Restart
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_client_live_reconfigure() -> None:
    s = Settings(
        _env_file=None,
        llm_provider="ollama",
        groq_api_key=SecretStr("gsk_test"),
        gemini_api_key=SecretStr("AIza_test"),
    )
    client = LLMClient(s)
    assert client.provider == "ollama"

    with patch.object(GroqProvider, "validate", new_callable=AsyncMock) as mock_val:
        mock_val.return_value = None
        await client.reconfigure("groq", "llama-3.1-8b-instant")
        assert client.provider == "groq"
        assert client.model == "llama-3.1-8b-instant"
        mock_val.assert_called_once()

    with patch.object(GeminiProvider, "validate", new_callable=AsyncMock) as mock_val_gem:
        mock_val_gem.return_value = None
        await client.reconfigure("gemini", "gemini-1.5-pro")
        assert client.provider == "gemini"
        assert client.model == "gemini-1.5-pro"
        mock_val_gem.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Fallback Behavior: Explicit Opt-In Only (Never Silent)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_llm_client_fallback_disabled_raises_immediately() -> None:
    s = Settings(_env_file=None, llm_provider="ollama", llm_model="llama3.2")
    prefs = {"llm_fallback_enabled": False, "llm_fallback_provider": "groq"}
    client = LLMClient(s, prefs)

    async def failing_op() -> str:
        raise LLMUnavailableError("Ollama is not running")

    with pytest.raises(LLMUnavailableError):
        await client._run_with_fallback("ollama", "llama3.2", failing_op)


@pytest.mark.asyncio
async def test_llm_client_fallback_enabled_triggers_transparent_retry() -> None:
    s = Settings(
        _env_file=None,
        llm_provider="ollama",
        groq_api_key=SecretStr("gsk_test"),
    )
    prefs = {"llm_fallback_enabled": True, "llm_fallback_provider": "groq"}
    client = LLMClient(s, prefs)

    attempts = []

    async def op() -> str:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise LLMUnavailableError("Primary failed")
        return "fallback result"

    result = await client._run_with_fallback("ollama", "llama3.2", op)
    assert result == "fallback result"
    assert len(attempts) == 2


# ---------------------------------------------------------------------------
# 6. Language Persona & English Enforcement
# ---------------------------------------------------------------------------
def test_persona_language_instructions() -> None:
    p_en = persona("concise", "english")
    assert "ALWAYS respond strictly and consistently in English" in p_en
    assert "Never respond in Hindi or Hinglish when in English mode" in p_en
    assert "Preserve proper names, technical terms, code" in p_en

    p_hinglish = persona("concise", "hinglish")
    assert "natural conversational Hinglish" in p_hinglish
    assert "written in Latin script" in p_hinglish

    p_hindi = persona("concise", "hindi")
    assert "Hindi (Devanagari script)" in p_hindi


# ---------------------------------------------------------------------------
# 7. Speech Pipeline English Guard (Hindi Translation)
# ---------------------------------------------------------------------------
class DummySpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def say(self, text: str, chunk: bool = True) -> None:
        self.spoken.append(text)

    async def say_stream(self, stream: AsyncIterator[str]) -> str:
        parts = []
        async for s in stream:
            parts.append(s)
        full = "".join(parts)
        self.spoken.append(full)
        return full

    async def interrupt(self) -> None:
        pass


class DummyLLM:
    def __init__(self) -> None:
        self.configured = True

    async def text(self, system: str, user: str, images: Any = None, max_tokens: int = 1000) -> str:
        if "Translate the following Hindi text" in system:
            return "Let's review your schedule and decide what to do today."
        return "English response"

    async def stream_text(self, system: str, user: str, max_tokens: int = 1000) -> AsyncIterator[str]:
        # Simulate returning Hindi Devanagari in English mode
        yield "आज का "
        yield "प्लान यह है"


@pytest.mark.asyncio
async def test_conversation_manager_english_guard_translates_hindi() -> None:
    speaker = DummySpeaker()
    llm = DummyLLM()
    browser = MagicMock()
    browser.is_open = False
    mgr = ConversationManager(
        tasks=MagicMock(),
        llm=llm,
        repo=MagicMock(),
        permissions=MagicMock(),
        browser=browser,
        speaker=speaker,
        prefs={"response_language": "english", "response_style": "concise"},
    )

    # Directly test say with Hindi text in English mode
    await mgr.say("मुझे React समझाओ।")
    assert len(speaker.spoken) == 1
    assert speaker.spoken[0] == "Let's review your schedule and decide what to do today."

    # Test _answer stream with Devanagari tokens
    await mgr._answer("Noor, aaj kya plan hai?")
    assert len(speaker.spoken) == 2
    assert "Let's review your schedule and decide what to do today." in speaker.spoken[1]


# ---------------------------------------------------------------------------
# 8. TTS Voice Compatibility Verification
# ---------------------------------------------------------------------------
def test_tts_voice_compatibility() -> None:
    # Hindi mode with English voice must fallback to Hindi neural voice
    assert compatible_voice("en-US-AriaNeural", "hindi") == "hi-IN-MadhurNeural"
    assert compatible_voice("en-GB-RyanNeural", "hindi") == "hi-IN-MadhurNeural"
    assert compatible_voice("hi-IN-MadhurNeural", "hindi") == "hi-IN-MadhurNeural"
    assert compatible_voice("hi-IN-SwaraNeural", "hindi") == "hi-IN-SwaraNeural"

    # Hinglish mode with US English voice falls back to Indian neural voice
    assert compatible_voice("en-US-AriaNeural", "hinglish") == "hi-IN-MadhurNeural"
    assert compatible_voice("en-IN-NeerjaNeural", "hinglish") == "en-IN-NeerjaNeural"
    assert compatible_voice("hi-IN-MadhurNeural", "hinglish") == "hi-IN-MadhurNeural"

    # English mode with Hindi voice falls back to natural English voice
    assert compatible_voice("hi-IN-MadhurNeural", "english") == "en-US-AriaNeural"
    assert compatible_voice("en-US-AriaNeural", "english") == "en-US-AriaNeural"


# ---------------------------------------------------------------------------
# 9. Speech Recognition Language Configuration (No Forced English)
# ---------------------------------------------------------------------------
def test_stt_language_setting_does_not_force_english() -> None:
    s = Settings(_env_file=None, response_language="english")
    # For English mode, Whisper language hint must be empty string (auto-detect)
    # so Hindi and Hinglish speech are not forced into English phonemes
    assert s.stt_language_for("english") == ""
    assert s.stt_language_for("hinglish") == ""
    assert s.stt_language_for("hindi") == "hi"
