"""Voice Activity Detection: finds where the user starts and stops speaking in a continuous PCM stream.

`EnergyVAD` is dependency-light (numpy only): an adaptive noise floor, *hysteresis* (a higher bar to start speech than to keep it going),
a bridge over short dips inside words, pre-roll so the first word is never cut off, and a configurable end-of-speech silence timeout.
Swap in another engine (Silero, WebRTC) by implementing the same `process()/snapshot()/reset()` surface in `build_vad()`.

Every tunable is a constructor argument fed from configuration (`VAD_*` env vars / Settings page); none are hardcoded in callers.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from backend.voice.audio_utils import SAMPLE_RATE

FRAME_MS = 20
MAX_GAP_FRAMES = 5


@dataclass
class VadConfig:
    speech_start_threshold: float = 0.012
    speech_end_threshold: float = 0.008
    silence_timeout_ms: int = 900
    minimum_speech_ms: int = 200
    maximum_utterance_s: float = 20.0
    preroll_ms: int = 400


@dataclass
class VadEvent:
    kind: str  # "start" | "end"
    audio: bytes = b""


class EnergyVAD:
    def __init__(self, config: VadConfig | None = None, sample_rate: int = SAMPLE_RATE, **kw: Any) -> None:
        self.cfg = config or VadConfig()
        if "end_silence_ms" in kw:
            self.cfg.silence_timeout_ms = kw["end_silence_ms"]
        for k, v in kw.items():
            if hasattr(self.cfg, k):
                setattr(self.cfg, k, v)
        self.sample_rate = sample_rate
        self.sensitivity = 1.0
        self.level = 0.0
        self.noise_floor = 0.005
        self._frame_bytes = int(sample_rate * (FRAME_MS / 1000.0) * 2)
        self._buf = bytearray()
        self._preroll: deque[bytes] = deque(maxlen=max(1, self.cfg.preroll_ms // FRAME_MS))
        self.reset()

    def configure(self, **changes: Any) -> None:
        for key, value in changes.items():
            if hasattr(self.cfg, key):
                setattr(self.cfg, key, type(getattr(self.cfg, key))(value))
        expected_len = max(1, self.cfg.preroll_ms // FRAME_MS)
        if self._preroll.maxlen != expected_len:
            self._preroll = deque(self._preroll, maxlen=expected_len)

    def reset(self) -> None:
        self._buf.clear()
        self._preroll.clear()
        self.in_speech = False
        self._voiced_run = 0
        self._gap = 0
        self._silence_ms = 0
        self._pending: list[bytes] = []
        self._seg = bytearray()

    def snapshot(self) -> bytes:
        return bytes(self._seg)

    def process(self, pcm: bytes) -> list[VadEvent]:
        self._buf += pcm
        events: list[VadEvent] = []
        fb = self._frame_bytes
        while len(self._buf) >= fb:
            frame = bytes(self._buf[:fb])
            del self._buf[:fb]
            events.extend(self._frame(frame))
        return events

    def _frame(self, frame: bytes) -> list[VadEvent]:
        c = self.cfg
        x = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
        r = float(np.sqrt(np.mean(x * x)))
        self.level = r
        thresh_start = max(c.speech_start_threshold * self.sensitivity, self.noise_floor * 2.5)
        thresh_end = max(c.speech_end_threshold * self.sensitivity, self.noise_floor * 1.5)
        thresh = thresh_end if self.in_speech else thresh_start
        voiced = r >= thresh

        if not self.in_speech:
            if not voiced:
                if self._voiced_run > 0:
                    self._gap += 1
                    if self._gap <= MAX_GAP_FRAMES:
                        return []
                    self._pending.clear()
                    self._voiced_run = 0
                self.noise_floor = min(0.05, max(0.0005, 0.97 * self.noise_floor + 0.03 * r))
                self._preroll.append(frame)
                self._gap = 0
                return []
            else:
                self._voiced_run += 1
                self._gap = 0
                self._pending.append(frame)
                if self._voiced_run * FRAME_MS >= c.minimum_speech_ms:
                    self.in_speech = True
                    self._silence_ms = 0
                    self._seg = bytearray(b"".join(self._preroll) + b"".join(self._pending))
                    self._pending.clear()
                    self._preroll.clear()
                    return [VadEvent("start")]
                return []
        else:
            self._seg += frame
            self._silence_ms = 0 if voiced else self._silence_ms + FRAME_MS
            too_long = len(self._seg) >= c.maximum_utterance_s * self.sample_rate * 2
            if self._silence_ms >= c.silence_timeout_ms or too_long:
                audio = bytes(self._seg)
                self.in_speech = False
                self._seg = bytearray()
                self._voiced_run = 0
                self._silence_ms = 0
                return [VadEvent("end", audio)]
            return []


def build_vad(engine: str = "energy", config: VadConfig | None = None) -> EnergyVAD:
    if engine in ("energy", ""):
        return EnergyVAD(config)
    raise ValueError(f"Unsupported VAD_ENGINE '{engine}' (available: energy)")


__all__ = ["VadConfig", "VadEvent", "EnergyVAD", "build_vad"]
