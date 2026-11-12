"""Per-turn latency measurements for the voice pipeline (shown in developer mode).

Marks, in order of a typical turn:
  speech_start → speech_end → stt_complete → llm_first_token → tts_first_audio → task_start → task_complete
Derived latencies are measured from `speech_end` (what the user feels as "how long until NOOR reacts").
"""
from __future__ import annotations

import time
from typing import Any

MARKS = ('speech_start', 'speech_end', 'stt_complete', 'llm_first_token', 'tts_first_audio', 'task_start', 'task_complete')


class TurnMetrics:
    def __init__(self, hub: Any) -> None:
        self.hub = hub
        self.turn = 0
        self.t: dict[str, float] = {}
        self.history: list[dict[str, Any]] = []

    def begin(self) -> None:
        self.turn += 1
        self.t = {}

    def mark(self, name: str) -> None:
        if name not in MARKS:
            raise ValueError(name)
        if name == "speech_start":
            self.begin()
        self.t.setdefault(name, time.perf_counter())

    def report(self) -> dict[str, Any]:
        t = self.t
        base = t.get("speech_end") or t.get("speech_start") or (min(t.values()) if t else 0.0)

        def ms(a: str, b: str | None = None) -> int | None:
            start = t.get(a) if b else base
            end = t.get(b or a)
            if start is None or end is None:
                return None
            return int((end - start) * 1000)

        return {
            "turn": self.turn,
            "marks_ms": {k: int((v - base) * 1000) for k, v in t.items()},
            "latency_ms": {
                "stt": ms("speech_end", "stt_complete"),
                "to_first_token": ms("speech_end", "llm_first_token"),
                "to_first_audio": ms("speech_end", "tts_first_audio"),
                "task_duration": ms("task_start", "task_complete"),
            },
        }

    async def emit(self) -> None:
        rep = self.report()
        if not self.history or rep["turn"] != self.history[-1]["turn"]:
            self.history = (self.history + [rep])[-20:]
        else:
            self.history = self.history[:-1] + [rep]
        await self.hub.broadcast({"type": "metrics.turn", **rep})

    async def mark_and_emit(self, name: str) -> None:
        self.mark(name)
        await self.emit()
