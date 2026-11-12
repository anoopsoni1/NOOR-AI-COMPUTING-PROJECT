"""Speech-to-text providers (replaceable via STT_PROVIDER).

  openai / openai_compatible : hosted Whisper through an OpenAI-style /audio/transcriptions endpoint
  local_whisper                     : on-device faster-whisper (no network; used for wake word + interruptions)

Transcription works on whole VAD-segmented utterances. Live captions while the user is still speaking come
from the local model (`voice.interim`), so hosted STT is only called once per finished utterance.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from backend.config import Settings
from backend.voice.audio_utils import SAMPLE_RATE, duration_s, pcm_to_float32, pcm_to_wav

log = logging.getLogger("noor.stt")


class STTError(RuntimeError):
    pass


class STTProvider(Protocol):
    name: str
    is_local: bool

    async def transcribe(self, pcm: bytes, prompt: str = "") -> str: ...


class RemoteWhisperSTT:
    is_local = False

    def __init__(self, name: str, base_url: str, api_key: str, model: str, language: str) -> None:
        self.name, self.base_url, self.model, self.language = name, base_url.rstrip("/"), model, language
        self._key = api_key
        self._http = httpx.AsyncClient(timeout=30)

    async def transcribe(self, pcm: bytes, prompt: str = "") -> str:
        if duration_s(pcm) < 0.2:
            return ""
        data = {"model": self.model, "response_format": "json", "temperature": "0"}
        if self.language:
            data["language"] = self.language
        if prompt:
            data["prompt"] = prompt
        try:
            r = await self._http.post(f"{self.base_url}/audio/transcriptions", data=data,
                                      files={"file": ("speech.wav", pcm_to_wav(pcm), "audio/wav")},
                                      headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise STTError(f"{self.name} STT HTTP {exc.response.status_code}: {exc.response.text[:160]}") from None
        except httpx.HTTPError as exc:
            raise STTError(f"{self.name} STT network error: {type(exc).__name__}") from None
        return str(r.json().get("text", "")).strip()


class LocalWhisperSTT:
    is_local = True
    name = "local_whisper"

    def __init__(self, model_name: str, language: str) -> None:
        self.model_name, self.language = model_name, language
        self._model: Any = None
        self._lock: asyncio.Lock | None = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def installed() -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def _load(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info("Loading local speech model '%s' (first run downloads it)", self.model_name)
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
            dev = "cuda" if has_cuda else "cpu"
            ct = "float16" if has_cuda else "int8"
            try:
                log.info("Loading speech model on %s (%s)", dev, ct)
                self._model = WhisperModel(self.model_name, device=dev, compute_type=ct)
            except Exception as exc:
                log.warning("CUDA Whisper failed (%s), falling back to CPU", exc)
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def _run(self, pcm: bytes, prompt: str) -> str:
        model = self._load()
        segments, _ = model.transcribe(pcm_to_float32(pcm), language=self.language or None, beam_size=1, vad_filter=False,
                                       condition_on_previous_text=False, initial_prompt=prompt or None, temperature=0.0)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, pcm: bytes, prompt: str = "") -> str:
        if duration_s(pcm) < 0.2:
            return ""
        async with self.lock:  # one inference at a time
            return await asyncio.to_thread(self._run, pcm, prompt)

    async def warm_up(self) -> None:
        await asyncio.to_thread(self._load)


@dataclass
class SttBundle:
    primary: STTProvider | None  # final transcripts of commands
    local: STTProvider | None  # wake word, interruption checks, interim captions

    @property
    def any(self) -> STTProvider | None:
        return self.local or self.primary


def build_stt(s: Settings) -> SttBundle:
    stt_lang = s.stt_language_for()
    local_model = "tiny" if s.response_language in ("hindi", "hinglish") else s.local_stt_model
    local: STTProvider | None = LocalWhisperSTT(local_model, stt_lang) if LocalWhisperSTT.installed() else None
    provider = s.stt_provider
    if provider == "auto":
        provider = "openai" if s.llm_provider == "openai" and s.llm_configured else "local_whisper"
    primary: STTProvider | None
    if provider == "local_whisper":
        primary = local
    elif provider in ("openai", "openai_compatible"):
        if not s.llm_configured:
            primary = local
        else:
            base = s.stt_base_url or (s.llm_base_url if provider == "openai_compatible" else "") or "https://api.openai.com/v1"
            model = s.stt_model or "whisper-1"
            primary = RemoteWhisperSTT(provider, base, s.llm_api_key.get_secret_value(), model, stt_lang)
    else:
        raise ValueError(f"Unsupported STT_PROVIDER '{provider}' (openai | openai_compatible | local_whisper | auto)")
    return SttBundle(primary=primary, local=local)


__all__ = ["STTProvider", "STTError", "SttBundle", "build_stt", "SAMPLE_RATE"]

