"""Audio manager: the voice state machine, speech output queue and barge-in.
  IDLE ─(wake word)→ WAKE_DETECTED → LISTENING ─(end of speech)→ PROCESSING → EXECUTING → SPEAKING → IDLE
                                         ↑___________ conversation window: follow-ups need no wake word ___|
Privacy: microphone frames come from the local UI over the local WebSocket. While idle they are only seen by the local VAD and the
on-device wake-word model; audio that does not contain "Hey NOOR" is dropped and nothing leaves the machine. After the wake word only the
finished utterance is sent to the configured STT provider.
This module owns *audio and turn-taking only*. It never touches the computer: commands go to the ConversationManager, which talks to the agent.
"""
from __future__ import annotations
import asyncio
import base64
import logging
import re
import time
from collections import deque
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, AsyncIterator, Callable
from backend.config import Settings
from backend.voice.audio_utils import duration_s, estimate_audio_seconds
from backend.voice.metrics import TurnMetrics
from backend.voice.speech_to_text import STTError, SttBundle
from backend.voice.text_to_speech import SentenceChunker, TTSError, TTSProvider
from backend.voice.vad import VadConfig, build_vad
from backend.voice.wake_word import WAKE_PROMPT, WakeWordDetector
log = logging.getLogger("noor.voice")

class VoiceState(str, Enum):
    IDLE = "IDLE"
    WAKE_DETECTED = "WAKE_DETECTED"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"

def _norm(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())

# ======================================================================================================================

class Speaker:
    """Sentence-level streaming TTS. Sentences are synthesized one after another and shipped to the UI as soon as each is ready."""

    def __init__(self, hub: Any, providers: list[TTSProvider], on_change: Callable[[], None]) -> None:
        self.hub, self.providers, self.on_change = hub, providers, on_change
        self._custom_providers = bool(providers and any(hasattr(p, "spoken") for p in providers))
        self.enabled = True
        self.metrics: TurnMetrics | None = None
        self.epoch = 0  # bumped on every interrupt; stale audio from older epochs is discarded by backend and UI alike
        self._q: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._synth_task: asyncio.Task[Any] | None = None
        self._synthesizing = False
        self._client_playing = False
        self._announced = False
        self._deadline = 0.0
        self._warned = False
        self._cache: dict[str, tuple[bytes, str]] = {}
        self._recent: deque[tuple[float, str]] = deque(maxlen=8)  # what NOOR said lately, for echo detection

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="noor-speaker")

    async def stop_worker(self) -> None:
        if self._worker:
            self._worker.cancel()

    async def stop(self) -> None:
        await self.stop_worker()

    def configure(self, voice: str | None = None, rate: str | None = None, provider: str | None = None, lang: str | None = None) -> None:
        """Change voice / speed / provider live (Settings page). Re-builds chain if provider changed."""
        try:
            from backend.voice.tts_manager import TTSManager
            mgr = TTSManager.get_instance()
            sp = 1.0
            if rate:
                try:
                    sp = 1.0 + float(rate.replace("%", "").replace("+", "")) / 100.0
                except ValueError:
                    sp = 1.0
            mgr.update_config(provider=provider, voice=voice, speed=sp, lang=lang)
            if not any(hasattr(p, "spoken") for p in self.providers):
                self.providers = mgr.build_chain()
            self._cache.clear()
        except Exception as exc:
            changed = False
            for p in self.providers:
                if voice and hasattr(p, "voice") and getattr(p, "voice") != voice:
                    p.voice, changed = voice, True
                if rate and hasattr(p, "rate") and getattr(p, "rate") != rate:
                    p.rate, changed = rate, True
            if changed:
                self._cache.clear()
    @property

    def available(self) -> bool:
        return bool(self.providers)
    @property

    def active(self) -> bool:
        return self._synthesizing or self._client_playing or not self._q.empty()
    # ---- echo guard -----------------------------------------------------------------------------------------------

    def looks_like_echo(self, heard: str, threshold: float = 0.6) -> bool:
        """True when `heard` is (mostly) what NOOR itself said in the last seconds, i.e. speaker output leaking into the microphone."""
        words = _norm(heard)
        if len(words) < 2:
            return False
        now = time.monotonic()
        recent = [t for ts, t in self._recent if now - ts < 20]
        if not recent:
            return False
        spoken = _norm(" ".join(recent))
        heard_s, spoken_s = " ".join(words), " ".join(spoken)
        overlap = sum(w in set(spoken) for w in words) / len(words)
        longest = SequenceMatcher(None, heard_s, spoken_s).find_longest_match(0, len(heard_s), 0, len(spoken_s)).size / max(1, len(heard_s))
        return (len(words) >= 3 and overlap >= 0.8) or longest >= threshold + 0.2
    # ---- producing speech -----------------------------------------------------------------------------------------

    async def say(self, text: str, chunk: bool = True) -> None:
        epoch = self.epoch
        if chunk:
            chunker = SentenceChunker()
            for sentence in chunker.feed(text + "\n") + chunker.flush():
                await self._queue(epoch, sentence)
        else:
            await self._queue(epoch, text)
        await self.hub.broadcast({"type": "assistant.message", "text": text})

    async def say_stream(self, deltas: AsyncIterator[str]) -> str:
        """Consume an LLM token stream, speaking each sentence as soon as it is complete. Returns the full text."""
        epoch, chunker, parts = self.epoch, SentenceChunker(), []
        try:
            async for delta in deltas:
                if epoch != self.epoch:  # interrupted: stop pulling tokens
                    break
                if not parts and self.metrics:
                    await self.metrics.mark_and_emit("llm_first_token")
                parts.append(delta)
                for sentence in chunker.feed(delta):
                    await self._queue(epoch, sentence)
            if epoch == self.epoch:
                for sentence in chunker.flush():
                    await self._queue(epoch, sentence)
        finally:
            aclose = getattr(deltas, "aclose", None)
            if aclose:
                await aclose()
        text = "".join(parts)
        await self.hub.broadcast({"type": "assistant.message", "text": text})
        return text

    async def _queue(self, epoch: int, sentence: str) -> None:
        if epoch != self.epoch:
            return
        self._recent.append((time.monotonic(), sentence))
        await self.hub.broadcast({"type": "assistant.delta", "text": sentence})
        if self.enabled and self.providers:
            self._q.put_nowait((epoch, sentence))
            if self._q.qsize() > 1 and sentence not in self._cache and not getattr(self, "_custom_providers", False):
                asyncio.create_task(self._prefetch(sentence, epoch))
            self.on_change()

    async def _prefetch(self, sentence: str, epoch: int) -> None:
        try:
            if epoch == self.epoch and sentence not in self._cache:
                await self._synthesize(sentence)
        except Exception:
            pass
    # ---- barge-in ---------------------------------------------------------------------------------------------------------

    async def interrupt(self) -> None:
        """Silence NOOR immediately: drop queued sentences, abort synthesis, tell the UI to flush playback."""
        was_active = self.active
        self.epoch += 1
        while not self._q.empty():
            self._q.get_nowait()
        if self._synth_task and not self._synth_task.done():
            self._synth_task.cancel()
        self._client_playing, self._announced = False, False
        await self.hub.broadcast({"type": "tts.stop", "epoch": self.epoch})
        if was_active:
            await self.hub.broadcast({"type": "tts.finished", "interrupted": True})
        self.on_change()

    async def duck(self, on: bool) -> None:
        await self.hub.broadcast({"type": "tts.duck", "on": on})

    def client_idle(self, epoch: int) -> None:
        """UI reports that everything it was given has finished playing."""
        if epoch == self.epoch and self._q.empty() and not self._synthesizing and self._client_playing:
            self._finish_playback()

    def _finish_playback(self) -> None:
        self._client_playing, self._announced = False, False
        asyncio.get_running_loop().create_task(self.hub.broadcast({"type": "tts.finished"}))
        self.on_change()
    # ---- worker ---------------------------------------------------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            epoch, text = await self._q.get()
            if epoch != self.epoch:
                continue
            self._synthesizing = True
            self.on_change()
            self._synth_task = asyncio.create_task(self._synthesize(text))
            await asyncio.wait({self._synth_task})
            result = None if self._synth_task.cancelled() else self._synth_task.result()
            self._synthesizing = False
            if result and epoch == self.epoch:
                await self._send(result[0], result[1], epoch)
            self.on_change()

    async def warm_cache(self, phrases: list[str]) -> None:
        """Pre-synthesize frequent short replies so "Yes?" / "Stopped." start instantly (and work if the network drops)."""
        for phrase in phrases:
            if phrase not in self._cache:
                await self._synthesize(phrase)

    async def _synthesize(self, text: str) -> tuple[bytes, str] | None:
        if text in self._cache:
            return self._cache[text]
        for provider in self.providers:
            try:
                audio = await provider.synth(text)
                if len(text) <= 60:
                    self._cache[text] = audio
                    if len(self._cache) > 64:
                        self._cache.pop(next(iter(self._cache)))
                return audio
            except TTSError as exc:
                log.warning("TTS provider %s failed: %s", provider.name, exc)
        if not self._warned:
            self._warned = True
            await self.hub.broadcast({"type": "voice.error", "message": "Text-to-speech is unavailable; showing captions only."})
        return None

    async def _send(self, audio: bytes, mime: str, epoch: int) -> None:
        if not self._announced:
            self._announced = True
            await self.hub.broadcast({"type": "tts.started"})
            if self.metrics and "tts_first_audio" not in self.metrics.t:
                await self.metrics.mark_and_emit("tts_first_audio")
        self._client_playing = True
        now = time.monotonic()
        self._deadline = max(now, self._deadline) + estimate_audio_seconds(audio, mime)
        await self.hub.broadcast({"type": "tts.chunk", "epoch": epoch, "mime": mime, "data": base64.b64encode(audio).decode()})
        asyncio.get_running_loop().call_later(self._deadline - now + 4.0, self._watchdog, epoch, self._deadline)

    def _watchdog(self, epoch: int, deadline: float) -> None:
        """If the UI never acknowledges playback (closed window...), do not stay in SPEAKING forever."""
        if epoch == self.epoch and self._client_playing and deadline == self._deadline and self._q.empty() and not self._synthesizing:
            self._finish_playback()

# ======================================================================================================================

class AudioManager:

    def __init__(self, settings: Settings, hub: Any, stt: SttBundle, speaker: Speaker, tasks: Any, prefs: dict[str, Any],
                 permissions: Any = None) -> None:
        self.s, self.hub, self.stt, self.speaker, self.tasks, self.permissions = settings, hub, stt, speaker, tasks, permissions
        self.vad = build_vad(settings.vad_engine, VadConfig(
            speech_start_threshold=settings.vad_speech_start_threshold, speech_end_threshold=settings.vad_speech_end_threshold,
            silence_timeout_ms=settings.vad_silence_timeout_ms, minimum_speech_ms=settings.vad_min_speech_ms,
            maximum_utterance_s=settings.vad_max_utterance_s, preroll_ms=settings.vad_preroll_ms))
        self.wake = WakeWordDetector(settings.wake_phrase, settings.wake_sensitivity)
        self.metrics = TurnMetrics(hub)
        speaker.metrics = self.metrics
        self.conversation: Any = None
        self.mic_enabled = True
        self.processing = False
        self.local_ready = False
        self.local_error = ""
        self._wake_enabled = prefs.get("wake_word_enabled", settings.wake_word_enabled)
        self._window_s = float(prefs.get("conversation_window_s", settings.conversation_window_s))
        self._listen_until = 0.0
        self._window_len = settings.listen_timeout_s
        self._extend_window = False
        self._wake_flash_until = 0.0
        self._last_wake = 0.0
        self._last_command: tuple[str, float] = ("", 0.0)
        self._state = VoiceState.IDLE
        self._seg_kind = "idle"
        self._segments: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[Any]] = []
        self._interim_task: asyncio.Task[Any] | None = None
        speaker.on_change = self.request_recompute
        self.apply_prefs(prefs)
    # ---- lifecycle ----------------------------------------------------------------------------------------------------------

    def attach(self, conversation: Any) -> None:
        self.conversation = conversation
        conversation.metrics = self.metrics

    async def start(self) -> None:
        self.speaker.start()
        self.hub.subscribe(self._on_event)
        self._tasks.append(asyncio.create_task(self._segment_worker(), name="noor-segments"))
        if self.speaker.providers:
            phrases = ["Yes?", "Stopped.", "Continuing.", "Okay.", "Sure.", "On it.", "Paused. Say continue when you're ready."]
            self._tasks.append(asyncio.create_task(self.speaker.warm_cache(phrases), name="noor-tts-warm"))
        if self.stt.local is not None and hasattr(self.stt.local, "warm_up"):
            self._tasks.append(asyncio.create_task(self._warm_up(), name="noor-warmup"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.speaker.stop_worker()

    async def _warm_up(self) -> None:
        try:
            await self.stt.local.warm_up()  # type: ignore[union-attr]
            self.local_ready = True
            log.info("Local speech model ready (wake word active)")
        except Exception as exc:  # noqa: BLE001
            self.local_error = type(exc).__name__
            log.warning("Local speech model unavailable (%s); wake word disabled, use the orb to talk", self.local_error)
        await self._emit_state(force=True)

    def apply_prefs(self, prefs: dict[str, Any]) -> None:
        """Apply settings live: nothing needs a restart and the audio pipeline is never torn down."""
        self._wake_enabled = bool(prefs.get("wake_word_enabled", self._wake_enabled))
        self._window_s = float(prefs.get("conversation_window_s", self._window_s))
        self.wake.sensitivity = float(prefs.get("wake_sensitivity", self.wake.sensitivity))
        keys = ("speech_start_threshold", "speech_end_threshold", "silence_timeout_ms", "minimum_speech_ms", "maximum_utterance_s")
        self.vad.configure(**{k: prefs[k] for k in keys if k in prefs})
        self.speaker.enabled = bool(prefs.get("voice_replies", True)) and self.speaker.enabled
        rate = prefs.get("speaking_rate")
        from backend.voice.text_to_speech import compatible_voice
        voice = prefs.get("tts_voice")
        provider = prefs.get("tts_provider", getattr(self.s, "tts_provider", "kokoro"))
        lang = prefs.get("response_language", self.s.response_language)
        if voice and provider == "edge":
            voice = compatible_voice(voice, lang)
        self.speaker.configure(voice, None if rate is None else f"{int(rate):+d}%", provider=provider, lang=lang)
    @property

    def wake_available(self) -> bool:
        return self._wake_enabled and self.stt.local is not None and self.local_ready
    @property

    def mic_allowed(self) -> bool:
        return self.permissions is None or self.permissions.capability_enabled("MICROPHONE")
    @property

    def mic_paused(self) -> bool:
        # Mic is effectively paused during speaking (echo/recognition suppressed); manual mute still takes precedence.
        return bool(self.speaker.active and self.mic_enabled and self.mic_allowed)

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value, "engaged": self._state == VoiceState.LISTENING, "mic": self.mic_enabled and self.mic_allowed,
            "micAllowed": self.mic_allowed, "micPaused": self.mic_paused, "tts": self.speaker.enabled,
            "wakeWord": {"enabled": self._wake_enabled, "phrase": self.s.wake_phrase, "available": self.wake_available,
                         "model": getattr(self.stt.local, "model_name", None), "error": self.local_error},
            "stt": {"provider": getattr(self.stt.primary, "name", None), "local": self.stt.local is not None},
            "tts_provider": self.speaker.providers[0].name if self.speaker.providers else None,
            "vad": {"engine": self.s.vad_engine, "endSilenceMs": self.vad.cfg.silence_timeout_ms},
        }
    # ---- state ------------------------------------------------------------------------------------------------------------------

    def _compute_state(self) -> VoiceState:
        run = self.tasks.run
        task_active = self.tasks.active and run is not None
        waiting = task_active and run.task.status in ("WAITING_FOR_PERMISSION", "WAITING_FOR_USER")  # type: ignore[union-attr]
        now = time.monotonic()
        if self.speaker.active:
            return VoiceState.SPEAKING
        if self.processing:
            return VoiceState.PROCESSING
        if task_active and not waiting:
            return VoiceState.EXECUTING
        if waiting or now < self._listen_until:
            return VoiceState.LISTENING
        if now < self._wake_flash_until:
            return VoiceState.WAKE_DETECTED
        return VoiceState.IDLE

    def request_recompute(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self._emit_state())
        except RuntimeError:
            pass

    async def _emit_state(self, force: bool = False) -> None:
        # Open the follow-up window once NOOR has nothing left to do or say.
        if self._extend_window and not self.speaker.active and not self.processing and not (self.tasks.active and self.tasks.run):
            self._extend_window = False
            self._listen_until = time.monotonic() + self._window_len
            asyncio.get_running_loop().call_later(self._window_len + 0.1, self.request_recompute)
        state = self._compute_state()
        if state != self._state or force:
            self._state = state
            await self.hub.broadcast({"type": "voice.state", **self.status()})

    async def _on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "finished":
            if self.conversation:
                self.conversation.on_task_finished(self.tasks.snapshot() or {})
            self._extend_window, self._window_len = True, self._window_s
            await self.metrics.mark_and_emit("task_complete")
            outcome = {"COMPLETED": "completed", "FAILED": "failed", "CANCELLED": "cancelled"}.get(str(event.get("status")), "completed")
            await self.hub.broadcast({"type": f"task.{outcome}", "taskId": event.get("taskId")})
        if kind in ("task", "finished"):
            self.request_recompute()
    # ---- inputs from the UI ------------------------------------------------------------------------------------------------------

    def set_mic_enabled(self, on: bool) -> None:
        self.mic_enabled = on
        if not (on and self.mic_allowed):
            self.vad.reset()
        self.request_recompute()

    def set_tts_enabled(self, on: bool) -> None:
        self.speaker.enabled = on
        if not on:
            asyncio.get_running_loop().create_task(self.speaker.interrupt())
        self.request_recompute()

    async def activate(self) -> None:
        """User tapped the orb: listen for one command without a wake word."""
        if not self.mic_allowed:
            await self.hub.broadcast({"type": "voice.error", "message": "The microphone permission is turned off in Permissions."})
            return
        await self.speaker.interrupt()
        self._wake_flash_until = time.monotonic() + 1.0
        self._listen_until = time.monotonic() + self.s.listen_timeout_s
        await self.hub.broadcast({"type": "voice.started", "reason": "activate"})
        asyncio.get_running_loop().call_later(self.s.listen_timeout_s + 0.1, self.request_recompute)
        await self._emit_state()

    async def emergency_stop(self) -> None:
        """STOP button / voice stop: speech, listening turn, task and pending actions all end now."""
        self._listen_until = 0.0
        self._cancel_interim()
        if self.conversation:
            await self.conversation.stop_everything("Stopped.")
        else:
            await self.speaker.interrupt()
            await self.tasks.stop()
        await self._emit_state()

    async def submit_text(self, text: str, source: str = "voice") -> None:
        text = text.strip()
        if not text or self.conversation is None:
            return
        key, now = " ".join(_norm(text)), time.monotonic()
        if source == "voice" and key == self._last_command[0] and now - self._last_command[1] < 2.5:
            log.info("duplicate command ignored")  # same words again within 2.5 s: echo or a double segment
            return
        self._last_command = (key, now)
        if source != "voice":  # typed command: there is no speech to time, so the turn starts here
            self.metrics.begin()
            self.metrics.mark("speech_end")
        self.metrics.mark("stt_complete")
        self.processing = True
        await self.hub.broadcast({"type": "voice.transcribed", "text": text, "source": source})
        await self.hub.broadcast({"type": "voice.final_transcript", "text": text, "source": source})
        await self.hub.broadcast({"type": "agent.thinking"})
        await self.metrics.emit()
        await self._emit_state()
        try:
            await self.conversation.handle(text, source)
        except Exception:  # noqa: BLE001
            log.exception("conversation failed")
            await self.speaker.say("Sorry, something went wrong.")
        finally:
            self.processing = False
            self._extend_window, self._window_len = True, self._window_s
            await self._emit_state()
    # ---- microphone frames -------------------------------------------------------------------------------------------------------

    def _busy_kind(self) -> str:
        if self.speaker.active or self.processing or self._compute_state() == VoiceState.EXECUTING:
            return "busy"
        if not self._wake_enabled or not self.wake_available:
            return "engaged"
        return "engaged" if self._compute_state() == VoiceState.LISTENING else "idle"

    async def feed_audio(self, pcm: bytes) -> None:
        if not self.mic_enabled or not pcm or not self.mic_allowed:
            return
        self.vad.sensitivity = 2.5 if self.speaker.active else 1.0  # echo residue must not count as the user talking
        for ev in self.vad.process(pcm):
            if ev.kind == "start":
                self._seg_kind = self._busy_kind()
                self.metrics.mark("speech_start")
                if self._seg_kind == "busy" and self.speaker.active:
                    await self.speaker.duck(True)  # instantly quieter while we work out whether this is an interruption
                if self._seg_kind != "idle":
                    await self.hub.broadcast({"type": "voice.started", "reason": "speech", "kind": self._seg_kind})
                if self._seg_kind == "engaged" and self.s.stt_interim and self.stt.local is not None and self.local_ready:
                    self._interim_task = asyncio.create_task(self._interim_loop())
            else:
                self._cancel_interim()
                self.metrics.mark("speech_end")
                await self.hub.broadcast({"type": "voice.stopped"})
                self._segments.put_nowait((self._seg_kind, ev.audio))

    def _cancel_interim(self) -> None:
        if self._interim_task and not self._interim_task.done():
            self._interim_task.cancel()

    async def _interim_loop(self) -> None:
        """Streaming partial transcripts: re-transcribe the growing utterance on-device every ~1 s while the user is still speaking."""
        try:
            while self.vad.in_speech:
                await asyncio.sleep(1.0)
                audio = self.vad.snapshot()
                if audio and self.vad.in_speech:
                    text = await self.stt.local.transcribe(audio)  # type: ignore[union-attr]
                    if text:
                        await self.hub.broadcast({"type": "voice.partial_transcript", "text": text})
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.debug("partial transcription failed", exc_info=True)
    # ---- utterance handling ---------------------------------------------------------------------------------------------------------------

    async def _segment_worker(self) -> None:
        while True:
            kind, audio = await self._segments.get()
            try:
                await self._handle_segment(kind, audio)
            except STTError as exc:
                log.warning("%s", exc)
                await self.hub.broadcast({"type": "voice.error", "message": str(exc)})
            except Exception:  # noqa: BLE001
                log.exception("segment handling failed")
            finally:
                if kind == "busy":
                    await self.speaker.duck(False)

    async def _handle_segment(self, kind: str, audio: bytes) -> None:
        if duration_s(audio) < 0.35 or self.conversation is None:
            return
        log.info("utterance kind=%s seconds=%.1f state=%s", kind, duration_s(audio), self._state.value)  # content is never logged
        if kind == "idle":
            if not self.wake_available:
                kind = "engaged"
            elif time.monotonic() - self._last_wake < self.s.wake_cooldown_s:
                return
            text = await self.stt.local.transcribe(audio, WAKE_PROMPT)  # type: ignore[union-attr]
            match = self.wake.match(text, strict=True)
            if match is None:
                return  # not for NOOR: dropped locally, never uploaded
            self._last_wake = time.monotonic()
            await self._on_wake(audio, match.remainder)
            return
        if kind == "engaged":
            provider = self.stt.primary or self.stt.local
            if provider is None:
                await self.hub.broadcast({"type": "voice.error", "message": "No speech-to-text provider is configured."})
                return
            raw = await provider.transcribe(audio)
            if self.speaker.looks_like_echo(raw, self.s.echo_similarity):
                return
            text = self.wake.strip(raw)
            if text:
                await self.submit_text(text, "voice")
            return
        # busy: NOOR is speaking / working. Only wake-prefixed commands, control words and yes/no count — everything
        # else (echo, TV, other people) is ignored.
        provider = self.stt.local or self.stt.primary
        if provider is None:
            return
        from backend.conversation.commands import parse_confirmation, parse_control
        text = await provider.transcribe(audio, WAKE_PROMPT)
        if self.speaker.looks_like_echo(text, self.s.echo_similarity):
            log.info("ignored echo of NOOR's own voice")
            return
        match = self.wake.match(text, strict=False)
        body = match.remainder if match else text
        run = self.tasks.run
        pending = bool(run and self.tasks.active and run.task.status in ("WAITING_FOR_PERMISSION", "WAITING_FOR_USER"))
        control = parse_control(body)
        if not control and not match:  # ASR often hears the name as "Nor"/"Nur": accept it in front of a control command
            first, _, rest = text.partition(" ")
            if first.strip(".,!?").lower() in ("nor", "nur", "nour", "noor") and parse_control(rest):
                control, body = parse_control(rest), rest
        if control or (pending and parse_confirmation(body)):
            if control:
                await self.speaker.interrupt()
            await self.submit_text(body, "voice")
        elif match and body:  # "NOOR, <new request>" while speaking: barge-in
            await self.speaker.interrupt()
            await self.submit_text(await self._better_transcript(audio, body), "voice")

    async def _better_transcript(self, audio: bytes, fallback: str) -> str:
        if self.stt.primary is not None and not self.stt.primary.is_local:
            try:
                better = self.wake.match(await self.stt.primary.transcribe(audio), strict=False)
                if better and better.remainder:
                    return better.remainder
            except STTError:
                pass
        return fallback

    async def _on_wake(self, audio: bytes, local_remainder: str) -> None:
        self._wake_flash_until = time.monotonic() + 1.0
        await self.hub.broadcast({"type": "voice.started", "reason": "wake"})
        await self._emit_state()
        if len(local_remainder.split()) >= 2:  # "Hey NOOR, open Chrome" in one breath
            await self.submit_text(await self._better_transcript(audio, local_remainder), "voice")
            return
        self._window_len = self.s.listen_timeout_s
        self._extend_window = True
        await self.conversation.say("Yes?")
        await self._emit_state()
