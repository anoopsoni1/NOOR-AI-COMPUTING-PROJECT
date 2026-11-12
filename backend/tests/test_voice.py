"""Voice layer tests: commands, wake word, VAD, sentence streaming, barge-in and the full audio→agent pipeline
(scripted STT/TTS/LLM, no microphone, network or models)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import mongomock
import numpy as np
import pytest

from backend import tools  # noqa: F401
from backend.agent.planner import PlanOut, RecoveryOut, StepIn
from backend.agent.state import AgentState, Deps
from backend.config import Settings
from backend.conversation.commands import Control, is_done_signal, parse_confirmation, parse_control
from backend.conversation.manager import ConversationManager, TurnOut
from backend.database.repository import Repository
from backend.security.permissions import PermissionEngine, Risk
from backend.services.hub import EventHub
from backend.services.memory import MemoryOut
from backend.services.prefs import effective
from backend.services.tasks import TaskManager
from backend.tools.base import REGISTRY
from backend.tools.computer import Computer
from backend.vision.screen_analyzer import JudgeOut
from backend.voice.audio_manager import AudioManager, Speaker, VoiceState
from backend.voice.speech_to_text import SttBundle
from backend.voice.text_to_speech import SentenceChunker, speakable
from backend.voice.vad import EnergyVAD, VadConfig
from backend.voice.wake_word import WakeWordDetector

SANDBOX = Path.home() / "noor_pytest_voice"


# ---- helpers ------------------------------------------------------------------------------------------------------
def tone(seconds: float, amp: float = 0.3, freq: float = 220.0) -> bytes:
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype(np.int16).tobytes()


def silence(seconds: float) -> bytes:
    return bytes(int(16000 * seconds) * 2)


class RecordingHub(EventHub):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    async def broadcast(self, event: dict) -> None:
        self.events.append(event)
        for fn in self._listeners:
            await fn(event)

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["type"] == kind]

    @property
    def said(self) -> list[str]:
        return [e["text"] for e in self.of("assistant.message")]


class FakeLLM:
    configured = True

    def __init__(self, plans: list[Any] | None = None, turns: list[TurnOut] | None = None) -> None:
        self.plans, self.turns = list(plans or []), list(turns or [])
        self.classified: list[str] = []

    async def structured(self, system: str, user: str, schema: type, images=None, retries: int = 2):  # noqa: ANN001
        if schema is TurnOut:
            self.classified.append(user)
            return self.turns.pop(0)
        if schema in (PlanOut, RecoveryOut):
            return self.plans.pop(0)
        if schema is JudgeOut:
            return JudgeOut(met=True)
        if schema is MemoryOut:
            return MemoryOut()
        raise AssertionError(schema)

    async def text(self, *a: Any, **k: Any) -> str:
        return "Done."

    async def stream_text(self, system: str, user: str, max_tokens: int = 0):
        for tok in ["Done. ", "I finished ", "your request. ", "Anything else?"]:
            await asyncio.sleep(0)
            yield tok


class FakeTTS:
    name = "fake"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synth(self, text: str) -> tuple[bytes, str]:
        self.spoken.append(text)
        return b"\x00" * 4800, "audio/mpeg"


class FakeBrowser:
    is_open = False
    last_url = ""
    headless = True

    async def close(self) -> None:
        return None

    async def go_back(self):
        from backend.tools.base import ToolResult
        return ToolResult("go_back", "none", "no_page")


class FakeStt:
    def __init__(self, local: bool, texts: list[str]) -> None:
        self.is_local = local
        self.name = "fake_local" if local else "fake_remote"
        self.texts = list(texts)
        self.calls = 0

    async def transcribe(self, pcm: bytes, prompt: str = "") -> str:
        self.calls += 1
        if self.texts:
            return self.texts.pop(0)
        return ""


@pytest.fixture(autouse=True)
def sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir()
    yield
    shutil.rmtree(SANDBOX, ignore_errors=True)


def build(
    plans: list[Any] | None = None,
    turns: list[TurnOut] | None = None,
    local_texts: list[str] | None = None,
    remote_texts: list[str] | None = None,
    tts: bool = True,
):
    s = Settings(
        _env_file=None,
        llm_api_key="test",
        data_dir=SANDBOX / "data",
        approval_timeout_s=5,
        tts_provider="off",
        wake_word_enabled=True,
    )
    repo = Repository(mongomock.MongoClient()["t"])
    hub = RecordingHub()
    perms = PermissionEngine(s, repo)
    llm = FakeLLM(plans, turns)
    deps = Deps(
        settings=s,
        llm=llm,
        registry=REGISTRY,
        permissions=perms,
        repo=repo,
        hub=hub,
        browser=FakeBrowser(),
        computer=Computer(),
        analyzer=None,
        prefs=effective(s, {}),
    )
    tasks = TaskManager(deps)
    fake_tts = FakeTTS()
    speaker = Speaker(hub, [fake_tts] if tts else [], lambda: None)
    local = FakeStt(True, local_texts or [])
    remote = FakeStt(False, remote_texts or [])
    audio = AudioManager(
        s,
        hub,
        SttBundle(primary=remote, local=local),
        speaker,
        tasks,
        deps.prefs,
    )
    conv = ConversationManager(
        tasks=tasks,
        llm=llm,
        repo=repo,
        permissions=perms,
        browser=deps.browser,
        speaker=speaker,
        session_id="",
    )
    audio.attach(conv)
    audio.local_ready = True
    deps.speaker = speaker
    return audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm


def step(tool: str, args: dict, desc: str = "step", **kw: Any) -> StepIn:
    return StepIn(tool=tool, args=args, description=desc, **kw)


async def until(cond, timeout: float = 5.0) -> None:
    end = asyncio.get_event_loop().time() + timeout
    while not cond():
        if asyncio.get_event_loop().time() >= end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


# ---- tests --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("stop", Control.STOP),
        ("NOOR, stop.", Control.STOP),
        ("Noor stop that", Control.STOP),
        ("cancel that", Control.CANCEL),
        ("pause", Control.PAUSE),
        ("Noor, pause please", Control.PAUSE),
        ("continue", Control.RESUME),
        ("go on", Control.RESUME),
        ("what are you doing?", Control.STATUS),
        ("how much is left", Control.PROGRESS),
        ("repeat that", Control.REPEAT),
        ("go back", Control.BACK),
        ("stop the video on YouTube", None),
        ("open the back door settings", None),
        ("search for how to pause a download", None),
    ],
)
def test_control_commands(text: str, expected: Control | None):
    assert parse_control(text) == expected


def test_confirmations():
    assert parse_confirmation("Yes.") == "yes"
    assert parse_confirmation("yeah go ahead") is None
    assert parse_confirmation("Go ahead") == "yes"
    assert parse_confirmation("Noor, yes please") == "yes"
    assert parse_confirmation("no") == "no"
    assert parse_confirmation("open chrome") is None
    assert is_done_signal("I'm done") and is_done_signal("logged in") and not is_done_signal("open github")


@pytest.mark.parametrize(
    "text,remainder",
    [
        ("Hey NOOR, open Chrome and search for React", "open Chrome and search for React"),
        ("Hey Noor.", ""),
        ("hey nur open chrome", "open chrome"),
        ("Hay, Newer, what time is it", "what time is it"),
        ("Hey, Nour", ""),
    ],
)
def test_wake_word_matches(text: str, remainder: str):
    m = WakeWordDetector().match(text)
    assert m is not None and m.remainder == remainder


@pytest.mark.parametrize(
    "text",
    ["open chrome", "hey john open chrome", "I was reading about the noor", "", "hey there"],
)
def test_wake_word_rejects_other_speech(text: str):
    assert WakeWordDetector().match(text) is None


def test_wake_word_non_strict_allows_bare_name():
    m = WakeWordDetector().match("NOOR, stop", strict=False)
    assert m and m.remainder == "stop"
    assert WakeWordDetector().match("NOOR, stop", strict=True) is None


def test_vad_detects_speech_and_end_of_utterance():
    vad = EnergyVAD(end_silence_ms=600)
    events = vad.process(silence(0.5) + tone(1.0) + silence(1.0))
    kinds = [e.kind for e in events]
    assert kinds == ["start", "end"]
    audio = events[1].audio
    assert 1.5 < len(audio) / 32000 < 2.1


def test_vad_ignores_noise_and_clicks():
    vad = EnergyVAD()
    rng = np.random.default_rng(0)
    noise = (rng.normal(0, 0.003, 32000) * 32767).astype(np.int16).tobytes()
    assert vad.process(noise) == []
    assert vad.process(tone(0.06) + silence(1.5)) == []


def test_vad_sensitivity_rejects_quiet_echo_while_speaking():
    vad = EnergyVAD()
    vad.sensitivity = 2.5
    assert vad.process(tone(1.0, amp=0.02) + silence(1.2)) == []
    vad.sensitivity = 1.0
    events = vad.process(tone(1.0, amp=0.04) + silence(1.2))
    assert [e.kind for e in events] == ["start", "end"]


def test_sentence_chunker_streams_sentences_early():
    c = SentenceChunker()
    out = c.feed("Sure. I'll open Chrome") + c.feed(" and search for React. Then I will read the results") + c.flush()
    assert out == ["Sure.", "I'll open Chrome and search for React.", "Then I will read the results"]
    assert speakable("Open **https://react.dev** now, see [docs](http://x.y)") == "Open the link now, see docs"


async def test_speaker_streams_audio_per_sentence_and_interrupts():
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build()
    speaker.start()
    await speaker.say("Opening Chrome now. Then searching for React.")
    await until(lambda: len(fake_tts.spoken) == 2)
    assert fake_tts.spoken == ["Opening Chrome now.", "Then searching for React."]
    assert hub.of("tts.started") and speaker.active
    old_epoch = speaker.epoch
    await speaker.interrupt()
    assert speaker.epoch == old_epoch + 1
    assert not speaker.active
    await speaker.stop()


async def test_say_stream_stops_consuming_tokens_after_interrupt():
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build()
    speaker.start()
    consumed: list[str] = []

    async def deltas():
        for tok in ("First sentence here. ", "Second sentence here. ", "Third sentence here. "):
            consumed.append(tok)
            yield tok
            await asyncio.sleep(0.05)

    task = asyncio.create_task(speaker.say_stream(deltas()))
    await asyncio.sleep(0.07)
    await speaker.interrupt()
    await task
    spoken = " ".join(e["text"] for e in hub.of("assistant.delta"))
    assert "Third" not in spoken
    await speaker.stop()


async def test_new_task_resolves_references_and_speaks_ack_and_result():
    plan = PlanOut(understanding="x", spoken_ack="Sure. Creating the file.", steps=[step("create_file", {"path": "noor_pytest_voice/a.txt", "content": "hi"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(plans=[plan], turns=[TurnOut(intent="new_task", goal="Create a.txt in the sandbox")])
    await conv.handle("make me a file", "voice")
    await tasks._job
    assert tasks.run.task.status == AgentState.COMPLETED and (SANDBOX / "a.txt").exists()
    assert "Sure. Creating the file." in hub.said
    assert any("I finished" in s for s in hub.said)
    assert tasks.run.task.goal == "Create a.txt in the sandbox" and "make me a file" in llm.classified[0]


async def test_followup_receives_conversation_context():
    plan1 = PlanOut(understanding="x", spoken_ack="Sure.", steps=[step("make_directory", {"path": "noor_pytest_voice/d1"})])
    plan2 = PlanOut(understanding="x", spoken_ack="Ok.", steps=[step("make_directory", {"path": "noor_pytest_voice/d2"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan1, plan2],
        turns=[TurnOut(intent="new_task", goal="Make d1"), TurnOut(intent="follow_up_task", goal="Make d2 next to d1")],
    )
    await conv.handle("make folder d1", "voice")
    await tasks._job
    audio.attach(conv)
    conv.on_task_finished(tasks.snapshot())
    await conv.handle("now one more next to it", "voice")
    await tasks._job
    assert "Previous task: Make d1" in llm.classified[1] and "make folder d1" in llm.classified[1]
    assert ("Make d1" in tasks.run.context) or ("make folder d1" in tasks.run.context)
    assert (SANDBOX / "d2").is_dir()


async def test_question_is_answered_by_streaming_without_starting_a_task():
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(turns=[TurnOut(intent="question")])
    assert await conv.handle("what is the capital of France", "voice") == "question"
    assert not tasks.active and any("Anything else?" in s for s in hub.said)


async def test_stop_pause_continue_and_status_by_voice():
    plan = PlanOut(
        understanding="x",
        spoken_ack="On it.",
        steps=[
            step("ask_user", {"question": "Which folder?"}),
            step("create_file", {"path": "noor_pytest_voice/z.txt", "content": "x"}, "Write the file"),
        ],
    )
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(plans=[plan], turns=[TurnOut(intent="new_task", goal="make z")])
    await conv.handle("make z", "voice")
    await until(lambda: tasks.run.task.status == AgentState.WAITING_FOR_USER)
    assert "Which folder?" in hub.said
    await conv.handle("how much is left", "voice")
    assert "of 2 steps" in hub.said[-1]
    await conv.handle("what are you doing", "voice")
    assert "waiting for you" in hub.said[-1].lower()
    await conv.handle("pause", "voice")
    assert tasks.run.task.paused and "Paused" in hub.said[-1]
    await conv.handle("Documents", "voice")
    await asyncio.sleep(0.4)
    assert not (SANDBOX / "z.txt").exists()
    await conv.handle("continue", "voice")
    await tasks._job
    assert (SANDBOX / "z.txt").exists() and tasks.run.task.status == AgentState.COMPLETED


async def test_voice_stop_cancels_task_and_says_stopped():
    plan = PlanOut(understanding="x", steps=[step("ask_user", {"question": "Continue?"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(plans=[plan], turns=[TurnOut(intent="new_task", goal="wait")])
    await conv.handle("run the waiting task", "voice")
    await until(lambda: tasks.run.task.status == AgentState.WAITING_FOR_USER)
    await conv.handle("Noor, stop", "voice")
    assert tasks.run.task.status == AgentState.CANCELLED and hub.said[-1] == "Stopped."


async def test_voice_yes_approves_low_risk_but_not_high_risk_actions():
    (SANDBOX / "a.pdf").write_text("x")
    plan = PlanOut(
        understanding="x",
        spoken_ack="Ok.",
        steps=[step("move_files", {"moves": [{"source": "noor_pytest_voice/a.pdf", "destination": "noor_pytest_voice/PDFs/a.pdf"}]})],
    )
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(plans=[plan], turns=[TurnOut(intent="new_task", goal="organize")])
    await conv.handle("organize my pdfs", "voice")
    await until(lambda: tasks.run.task.pending_permission is not None)
    req = tasks.run.task.pending_permission
    assert req.voice_approvable and any("move" in s.lower() or "permission" in s.lower() for s in hub.said)
    await conv.handle("yes", "voice")
    await tasks._job
    assert (SANDBOX / "PDFs" / "a.pdf").exists()

    plan2 = PlanOut(understanding="x", steps=[step("send_email", {"to": "a@b.co", "subject": "Hello", "body": "Hi"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan2, RecoveryOut(decision="abort", reason="declined")],
        turns=[TurnOut(intent="new_task", goal="email")],
    )
    await conv.handle("email a@b.co", "voice")
    await until(lambda: tasks.run.task.pending_permission is not None)
    assert not tasks.run.task.pending_permission.voice_approvable
    await conv.handle("yes", "voice")
    assert tasks.run.task.pending_permission is not None
    assert "on screen" in hub.said[-1]
    perms.respond(tasks.run.task.pending_permission.id, False)
    await tasks._job


async def feed(audio: AudioManager, pcm: bytes) -> None:
    for i in range(0, len(pcm), 640):
        await audio.feed_audio(pcm[i : i + 640])
        await asyncio.sleep(0.001)


async def test_full_pipeline_wake_word_to_spoken_result():
    plan = PlanOut(understanding="x", spoken_ack="Sure. Making a folder.", steps=[step("make_directory", {"path": "noor_pytest_voice/made"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan],
        turns=[TurnOut(intent="new_task", goal="Make a folder")],
        local_texts=["Hey NOOR, make a folder called made."],
        remote_texts=["Hey NOOR, make a folder called made."],
    )
    await audio.start()
    try:
        await feed(audio, silence(0.3) + tone(1.5) + silence(1.2))
        await until(lambda: tasks.run is not None and tasks.run.task.status == AgentState.COMPLETED)
        assert (SANDBOX / "made").is_dir()
        assert hub.of("voice.started")[0]["reason"] == "wake"
        assert hub.of("voice.transcribed")[0]["text"].lower().startswith("make a folder")
        assert remote.calls == 1
        await until(lambda: "Sure. Making a folder." in fake_tts.spoken)
        await until(lambda: any("request" in s for s in fake_tts.spoken))
    finally:
        await audio.stop()


async def test_speech_without_wake_word_is_dropped_locally_and_never_uploaded():
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        local_texts=["did you see the game last night"],
    )
    await audio.start()
    try:
        await feed(audio, tone(1.5) + silence(1.2))
        await until(lambda: local.calls == 1)
        await asyncio.sleep(0.1)
        assert remote.calls == 0 and not hub.of("voice.transcribed") and not tasks.active
        assert audio.status()["state"] == VoiceState.IDLE.value
    finally:
        await audio.stop()


async def test_wake_word_alone_says_yes_then_listens_for_the_command_without_wake_word():
    plan = PlanOut(understanding="x", spoken_ack="Ok.", steps=[step("make_directory", {"path": "noor_pytest_voice/f"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan],
        turns=[TurnOut(intent="new_task", goal="make f")],
        local_texts=["Hey NOOR."],
        remote_texts=["Make a folder named f"],
    )
    await audio.start()
    try:
        await feed(audio, tone(1.0) + silence(1.2))
        await until(lambda: "Yes?" in fake_tts.spoken)
        speaker.client_idle(speaker.epoch)
        await until(lambda: audio.status()["state"] == "LISTENING")
        await feed(audio, tone(1.2) + silence(1.2))
        await until(lambda: tasks.run is not None and tasks.run.task.status == AgentState.COMPLETED)
        assert (SANDBOX / "f").is_dir()
    finally:
        await audio.stop()


async def test_barge_in_stop_while_noor_is_speaking():
    plan = PlanOut(understanding="x", steps=[step("ask_user", {"question": "Shall I continue?"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan],
        turns=[TurnOut(intent="new_task", goal="wait")],
        local_texts=["NOOR, stop."],
    )
    await audio.start()
    try:
        await conv.handle("run the waiting task", "voice")
        await until(lambda: speaker.active)
        await feed(audio, tone(1.2, amp=0.6) + silence(1.2))
        await until(lambda: tasks.run.task.status == AgentState.CANCELLED)
        assert hub.of("tts.stop") and hub.said[-1] == "Stopped."
        assert any(e.get("type") == "tts.duck" and e.get("on") for e in hub.events)
    finally:
        await audio.stop()


async def test_echo_while_speaking_is_ignored():
    plan = PlanOut(understanding="x", steps=[step("ask_user", {"question": "Shall I continue?"})])
    audio, conv, tasks, hub, speaker, perms, fake_tts, local, remote, llm = build(
        plans=[plan],
        turns=[TurnOut(intent="new_task", goal="wait")],
        local_texts=["shall i continue"],
    )
    await audio.start()
    try:
        await conv.handle("run the waiting task", "voice")
        await until(lambda: speaker.active)
        await feed(audio, tone(1.2, amp=0.6) + silence(1.2))
        await until(lambda: local.calls == 1)
        await asyncio.sleep(0.1)
        assert tasks.run.task.status == AgentState.WAITING_FOR_USER and speaker.active
        assert not hub.of("tts.stop")
    finally:
        await audio.stop()
