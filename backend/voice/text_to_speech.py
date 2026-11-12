"""Text-to-speech providers (replaceable via TTS_PROVIDER) and the sentence chunker that makes speech start early.

  edge          : Microsoft neural voices through the `edge-tts` package (free, needs internet)
  openai        : any OpenAI-style /audio/speech endpoint (OpenAI, or any compatible server)
  system        : offline Windows SAPI voices (PowerShell, no extra dependency)

Streaming model: the LLM's token stream is cut into sentences; each sentence is synthesized as soon as it is
complete and sent to the UI for immediate playback while the next one is being generated.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from backend.config import Settings

log = logging.getLogger("noor.tts")


class TTSError(RuntimeError):
    pass


class TTSProvider(Protocol):
    name: str

    async def synth(self, text: str) -> tuple[bytes, str]:
        """Return (audio bytes, mime type) for one short piece of text."""
        ...


class EdgeTTS:
    name = "edge"

    def __init__(self, voice: str, rate: str) -> None:
        self.voice, self.rate = voice, rate

    async def synth(self, text: str) -> tuple[bytes, str]:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("edge-tts is not installed (pip install edge-tts)") from exc
        out = bytearray()
        try:
            async for chunk in edge_tts.Communicate(text, self.voice, rate=self.rate).stream():
                if chunk["type"] == "audio":
                    out += chunk["data"]
        except Exception as exc:  # noqa: BLE001 - network / service errors
            raise TTSError(f"edge TTS failed: {type(exc).__name__}") from None
        if not out:
            raise TTSError("edge TTS returned no audio")
        return bytes(out), "audio/mpeg"


class OpenAICompatTTS:
    name = "openai"

    def __init__(self, base_url: str, api_key: str, model: str, voice: str) -> None:
        self.base_url, self.model, self.voice, self._key = base_url.rstrip("/"), model, voice, api_key
        self._http = httpx.AsyncClient(timeout=30)

    async def synth(self, text: str) -> tuple[bytes, str]:
        try:
            r = await self._http.post(f"{self.base_url}/audio/speech", headers={"Authorization": f"Bearer {self._key}"},
                                      json={"model": self.model, "voice": self.voice, "input": text, "response_format": "wav"})
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TTSError(f"TTS HTTP {exc.response.status_code}: {exc.response.text[:160]}") from None
        except httpx.HTTPError as exc:
            raise TTSError(f"TTS network error: {type(exc).__name__}") from None
        return r.content, "audio/wav"


class WindowsSapiTTS:
    """Offline fallback using System.Speech. The text is passed through an environment variable, never interpolated into the script."""
    name = "system"
    _SCRIPT = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
               "$s.SetOutputToWaveFile($env:NOOR_TTS_OUT); $s.Speak($env:NOOR_TTS_TEXT); $s.Dispose()")

    async def synth(self, text: str) -> tuple[bytes, str]:
        if sys.platform != "win32":
            raise TTSError("system TTS is only implemented for Windows")
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell", "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT,
                env={**os.environ, "NOOR_TTS_TEXT": text, "NOOR_TTS_OUT": path},
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), 20)
            data = Path(path).read_bytes()
        except (asyncio.TimeoutError, OSError) as exc:
            raise TTSError(f"system TTS failed: {type(exc).__name__}") from None
        finally:
            Path(path).unlink(missing_ok=True)
        if len(data) < 100:
            raise TTSError("system TTS produced no audio")
        return data, "audio/wav"


def build_tts_chain(s: Settings) -> list[TTSProvider]:
    """Primary provider first, then offline fallbacks, so NOOR can still talk if the network is down."""
    p = (s.tts_provider or "kokoro").lower()
    if p == "off":
        return []
    try:
        from backend.voice.tts_manager import TTSManager
        mgr = TTSManager.get_instance(s)
        mgr.update_config(provider=p, voice=s.tts_voice, lang=s.response_language)
        return mgr.build_chain()
    except Exception as exc:
        log.warning("Could not build TTS chain from TTSManager (%s), using legacy chain", exc)

    chain: list[TTSProvider] = []
    if p == "edge":
        raw_voice = s.tts_voice or _edge_voice_for(s.response_language)
        voice = compatible_voice(raw_voice, s.response_language)
        chain.append(EdgeTTS(voice, s.tts_rate))
    elif p in ("openai", "openai_compatible"):
        base = s.tts_base_url or (s.llm_base_url if p == "openai_compatible" else "") or "https://api.openai.com/v1"
        chain.append(OpenAICompatTTS(base, s.llm_api_key.get_secret_value(), s.tts_model or "tts-1", s.tts_voice or "alloy"))
    if sys.platform == "win32" and not any(isinstance(x, WindowsSapiTTS) for x in chain):
        chain.append(WindowsSapiTTS())
    return chain


def _edge_voice_for(lang: str) -> str:
    lang = (lang or "english").lower()
    if lang == "hindi":
        return "hi-IN-MadhurNeural"
    if lang == "hinglish":
        return "hi-IN-MadhurNeural"
    return "en-US-AriaNeural"


def compatible_voice(voice: str, lang: str) -> str:
    """Ensure the chosen TTS voice is compatible with the response language.
    
    If the selected voice cannot pronounce the chosen language, logs a warning
    and falls back to a high-quality compatible voice.
    """
    lang = (lang or "english").lower()
    voice = voice or _edge_voice_for(lang)
    v_lower = voice.lower()
    if lang == "hindi":
        if not (v_lower.startswith("hi-") or "hindi" in v_lower):
            log.warning("Selected voice '%s' does not support Hindi. Using compatible Hindi voice 'hi-IN-MadhurNeural'.", voice)
            return "hi-IN-MadhurNeural"
    elif lang == "hinglish":
        if not (v_lower.startswith("hi-") or v_lower.startswith("en-in") or "india" in v_lower):
            log.warning("Selected voice '%s' may not pronounce Hinglish naturally. Using 'hi-IN-MadhurNeural'.", voice)
            return "hi-IN-MadhurNeural"
    elif lang == "english":
        if v_lower.startswith("hi-"):
            log.warning("Selected voice '%s' is a Hindi voice. Using 'en-US-AriaNeural' for English.", voice)
            return "en-US-AriaNeural"
    return voice


# ---- text shaping -----------------------------------------------------------------------------------------
_URL = re.compile(r"https?://\S+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD = re.compile(r"[*_`#>]+")


def speakable(text: str) -> str:
    """Strip markdown/URLs so they are not read aloud."""
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _URL.sub("the link", text)
    text = _MD.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    """Turns a token stream into speakable sentences as early as possible."""

    _END = re.compile(r"(?<=[.!?…])\s+|\n+")

    _ABBREV = {"mr", "mrs", "ms", "dr", "vs", "etc", "e.g", "i.e", "st", "no"}

    def __init__(self, min_len: int = 5, max_len: int = 220) -> None:
        self.buf = ""
        self.min_len, self.max_len = min_len, max_len

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            m = self._END.search(self.buf)
            if m and m.start() >= self.min_len - 1:
                head = self.buf[: m.start()]
                last_word = head.rsplit(" ", 1)[-1].rstrip(".").lower()
                if last_word in self._ABBREV and head.endswith("."):  # "Dr. Smith": not a sentence end
                    nxt = self._END.search(self.buf, m.end())
                    if nxt is None:
                        break
                    m = nxt
                    head = self.buf[: m.start()]
                out.append(head.strip())
                self.buf = self.buf[m.end():]
                continue
            if len(self.buf) > self.max_len:  # long sentence without punctuation: break at the last comma/space
                cut = max(self.buf.rfind(", ", 0, self.max_len), self.buf.rfind(" ", 0, self.max_len))
                if cut > 0:
                    out.append(self.buf[: cut + 1].strip())
                    self.buf = self.buf[cut + 1:]
                    continue
            break
        return [x for x in (speakable(s) for s in out) if x]

    def flush(self) -> list[str]:
        rest, self.buf = speakable(self.buf), ""
        return [rest] if rest else []

