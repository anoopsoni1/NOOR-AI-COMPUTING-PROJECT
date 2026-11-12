"""Small PCM helpers. Wire format everywhere: 16 kHz, mono, signed 16-bit little-endian."""
from __future__ import annotations

import io
import wave
import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def pcm_to_float32(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    x = pcm_to_float32(pcm)
    return float(np.sqrt(np.mean(x * x)))


def duration_s(pcm: bytes) -> float:
    return len(pcm) / BYTES_PER_SECOND


def estimate_audio_seconds(data: bytes, mime: str) -> float:
    if mime in ("audio/wav", "audio/x-wav"):
        try:
            with wave.open(io.BytesIO(data)) as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError):
            pass
    return len(data) * 8 / 48000.0
