"""Wake word ("Hey NOOR") detection.

Detection is local: only speech segments found by the local VAD are transcribed *on-device* (faster-whisper), and the transcript is matched
against the wake phrase. Audio that does not contain the wake phrase is discarded and never sent to any remote service. The matcher is
fuzzy because ASR renders "NOOR" as "Nur", "Nour", "Newer", "Heynor"... `sensitivity` (0.5-0.95) sets how close a word must be to count:
higher = fewer accidental activations, lower = more forgiving.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

WAKE_PROMPT = "Hey NOOR."

_GREETINGS = frozenset({'hello', 'okay', 'hey', 'yo', 'hey,', 'hi', 'a', 'ok', 'hay', 'he', 'hei', 'suno', 'namaste', 'namaskar', 'oye', 'arre', 'listen', 'bhai'})
_NAME_ALIASES = frozenset({'noore', 'nur', 'nour', 'newer', 'noo', 'neur', 'nure', 'nora', 'nuh', 'noorr', 'noor', 'noir', 'nor', 'nuur'})


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


@dataclass
class WakeMatch:
    remainder: str
    strict: bool = True


class WakeWordDetector:
    def __init__(self, phrase: str = "hey noor", sensitivity: float = 0.75) -> None:
        parts = _tokens(phrase)
        self.name = parts[-1] if parts else "noor"
        self.phrase = phrase
        self.sensitivity = sensitivity

    def _is_name(self, tok: str) -> bool:
        if tok == self.name:
            return True
        if self.sensitivity <= 0.85 and tok in _NAME_ALIASES:
            return True
        return SequenceMatcher(None, tok, self.name).ratio() >= self.sensitivity

    def match(self, text: str, strict: bool = True) -> WakeMatch | None:
        words = text.split()
        toks = _tokens(text)
        if not toks:
            return None
        # look for compound greetings e.g. "heynoor", "oknoor", "sunonoor"
        for i, w in enumerate(toks[:3]):
            for g in _GREETINGS:
                if w.startswith(g) and len(w) > len(g) and self._is_name(w[len(g):]):
                    return WakeMatch(" ".join(words[i + 1:]).strip(), True)
        # look for greeting + name e.g. "hey noor", "suno noor"
        for i in range(min(4, len(toks))):
            if i + 1 < len(toks) and toks[i] in _GREETINGS and self._is_name(toks[i + 1]):
                return WakeMatch(" ".join(words[i + 2:]).strip(), True)
        if not strict and toks:
            for i in range(min(2, len(toks))):
                if self._is_name(toks[i]) and toks[i] != "nor":
                    return WakeMatch(" ".join(words[i + 1:]).strip(), False)
        return None

    def strip(self, transcript: str) -> str:
        m = self.match(transcript, strict=False)
        return m.remainder if m else transcript.strip()
