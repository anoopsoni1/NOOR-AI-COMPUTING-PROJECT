"""Conversation manager: turns what the user said (spoken or typed) into the right action.

For every utterance it decides between:
  control command   (stop / pause / continue / cancel / what are you doing / how much is left / repeat / go back)
  confirmation      (yes / no to a pending approval)
  answer            (reply to a question NOOR asked, e.g. "log in", "what's your username?")
  new task / follow-up task   (references like "the first result" are resolved against the conversation)
  question / small talk       (answered directly, streamed to speech)
and keeps short-term context (recent turns, previous task, the page that is open).
"""
from __future__ import annotations

import logging
import random
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from backend.agent.state import AgentState, StepStatus
from backend.conversation.commands import Control, is_done_signal, parse_confirmation, parse_control
from backend.database.repository import run_db
from backend.security.redaction import redact_text

log = logging.getLogger("noor.conversation")

def persona(style: str = "concise", language: str = "english") -> str:
    length = "one or two short sentences" if style == "concise" else "up to four natural sentences"
    lang = language.lower()
    if lang == "hindi":
        return (
            f"You are NOOR, a friendly voice-first AI computer agent. You speak aloud, so answer in {length}, in natural Hindi (Devanagari script), in the first person, without markdown, "
            "lists or URLs. Never translate proper names, code, commands, filenames, URLs, or technical identifiers unless the user explicitly asks. "
            "You can open websites, search, read pages, manage files and run tasks on the user's computer."
        )
    if lang == "hinglish":
        return (
            f"You are NOOR, a friendly voice-first AI computer agent. You speak aloud, so answer in {length}, in natural conversational Hinglish (mixing Hindi and English as people do in everyday conversation, written in Latin script for Hindi words), in the first person, without markdown, "
            "lists or URLs. Never translate proper names, code, commands, filenames, URLs, or technical identifiers unless the user explicitly asks. "
            "You can open websites, search, read pages, manage files and run tasks on the user's computer."
        )
    return (
        f"You are NOOR, a friendly voice-first AI computer agent. You speak aloud, so answer in {length}, in the first person, strictly in natural, fluent English, without markdown, "
        "lists or URLs. You must ALWAYS respond strictly and consistently in English, regardless of whether the user speaks Hindi, Hinglish, or English. "
        "Never respond in Hindi or Hinglish when in English mode. "
        "Preserve proper names, technical terms, code, filenames, commands, and URLs without translating them unnecessarily. "
        "You can open websites, search, read pages, manage files and run tasks on the user's computer."
    )


PERSONA = persona()
_IMPERATIVE = re.compile(r"^(please\s+)?(open|launch|start|search|find|go to|create|make|download|play|show|close|minimi[sz]e|maximi[sz]e|switch|type|write|"
                         r"organi[sz]e|move|rename|copy|set|increase|decrease|turn|run|read|look up|save)\b", re.I)
_GREETING = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening)|how are you|howdy|namaste)\b.*$", re.I)
_QUESTION = re.compile(r"^(what|why|how|who|where|when|can you explain|tell me about|define|describe|is it|are there)\b", re.I)
ACKS = ["Sure.", "On it.", "Okay."]


class TurnOut(BaseModel):
    intent: Literal["new_task", "follow_up_task", "question", "smalltalk"]
    goal: str = Field("", description="For task intents: a complete standalone instruction with every reference (it, that, the first result, ...) resolved")


@dataclass
class Turn:
    role: str  # "user" | "noor"
    text: str
    intent: str = ""
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationManager:
    def __init__(self, *, tasks: Any, llm: Any, repo: Any, permissions: Any, browser: Any, speaker: Any, session_id: str = "",
                 prefs: dict[str, Any] | None = None) -> None:
        self.tasks, self.llm, self.repo, self.permissions, self.browser = tasks, llm, repo, permissions, browser
        self.prefs = prefs if prefs is not None else {}
        self.metrics: Any = None
        self.voice = speaker  # the audio layer's Speaker (say / say_stream / interrupt)
        self.session_id = session_id
        self.history: deque[Turn] = deque(maxlen=12)
        self.last_spoken = ""
        self.previous_task: dict[str, str] | None = None
        self._cached_snap: dict[str, Any] | None = None
        self._cached_snap_time: float = 0.0

    # ---- speech facade (also handed to the agent as deps.speaker) -------------------------------------------
    async def say(self, text: str, chunk: bool = True, **kwargs: Any) -> None:
        lang = self.prefs.get("response_language", "english").lower()
        if lang == "english" and re.search(r"[\u0900-\u097F]", text):
            text = await self._ensure_english(text)
        self._record("noor", text)
        if hasattr(self.voice, "say"):
            await self.voice.say(text, chunk=chunk)

    async def say_stream(self, deltas: AsyncIterator[str]) -> str:
        text = await self.voice.say_stream(deltas)
        self._record("noor", text)
        return text

    async def interrupt(self) -> None:
        if hasattr(self.voice, "interrupt"):
            await self.voice.interrupt()

    async def duck(self, on: bool) -> None:
        if hasattr(self.voice, "duck"):
            await self.voice.duck(on)

    async def _ensure_english(self, text: str) -> str:
        """Translate Hindi text to fluent English if returned in English mode."""
        prompt = (
            "Translate the following Hindi text into natural, fluent conversational English. "
            "Preserve technical terms, code, filenames, commands, and proper names intact. "
            "Output only the direct English translation without commentary."
        )
        try:
            return (await self.llm.text(prompt, text)).strip()
        except Exception:
            return re.sub(r"[\u0900-\u097F]+", "", text).strip() or text

    def _record(self, role: str, text: str, intent: str = "") -> None:
        text = redact_text(text.strip())
        if not text:
            return
        if role == "noor":
            self.last_spoken = text
        self.history.append(Turn(role, text, intent))
        if self.session_id:  # best effort, fire and forget
            import asyncio

            asyncio.create_task(self._persist_turn(role, text, intent))

    async def _persist_turn(self, role: str, text: str, intent: str) -> None:
        try:
            await run_db(self.repo.append_turn, self.session_id, {"role": role, "text": text[:500], "intent": intent, "ts": datetime.now(timezone.utc)})
        except Exception:  # noqa: BLE001
            pass

    # ---- main entry --------------------------------------------------------------------------------------------------
    async def handle(self, text: str, source: str = "voice") -> str:
        """Process one user utterance. Returns the intent label (useful for tests/telemetry)."""
        self._record("user", text)
        run = self.tasks.run
        active = self.tasks.active and run is not None
        status = run.task.status if run else None

        # 1. Control commands: always first, instant, no LLM.
        control = parse_control(text)
        if control is not None:
            await self._control(control)
            return f"control:{control.value}"

        # 2. Yes/no for a pending approval.
        if active and status == AgentState.WAITING_FOR_PERMISSION and run.task.pending_permission:
            answer = parse_confirmation(text)
            if answer:
                await self._permission_answer(run.task.pending_permission, answer == "yes")
                return f"confirmation:{answer}"

        # 3. An answer to a question NOOR asked.
        if active and status == AgentState.WAITING_FOR_USER:
            self.tasks.respond("done" if is_done_signal(text) else text)
            return "answer"

        # 4. Anything else: understand it with the LLM, with conversation context.
        if not self.llm.configured:
            await self.say("I can't think without a language model. Please add your API key in the settings file.")
            return "error"
        if active:
            goal = run.task.goal if run else "something"
            await self.say(f"I'm still working on: {goal}. Say stop if you want me to drop it, or wait until I'm done.")
            return "busy"
        if self._obvious_new_task(text):  # first, self-contained command: skip the classifier round trip
            turn = TurnOut(intent="new_task", goal=text)
        elif _GREETING.match(text.strip()):
            turn = TurnOut(intent="smalltalk")
        elif not getattr(self.llm, "turns", None) and _QUESTION.match(text.strip()) and not getattr(self.browser, "is_open", False):
            turn = TurnOut(intent="question")
        else:
            try:
                turn = await self._classify(text)
            except Exception as exc:  # noqa: BLE001
                log.warning("classification failed: %s", type(exc).__name__)
                if _IMPERATIVE.match(text.strip()):
                    turn = TurnOut(intent="new_task", goal=text)
                else:
                    turn = TurnOut(intent="question")
        self.history[-1].intent = turn.intent

        if turn.intent in ("new_task", "follow_up_task"):
            await self.say(random.choice(ACKS))  # instant, cached acknowledgement; planning continues while it plays
            await self._start_task((turn.goal or text).strip())
        else:
            await self._answer(text, smalltalk=(turn.intent == "smalltalk"))
        return turn.intent

    # ---- control commands ------------------------------------------------------------------------------------------
    async def _control(self, control: Control) -> None:
        run = self.tasks.run
        active = self.tasks.active and run is not None
        if control is Control.STOP:
            await self.stop_everything("Stopped.")
        elif control is Control.CANCEL:
            if active:
                await self.stop_everything("Okay, I cancelled that.")
            else:
                await self.say("There's nothing to cancel.")
        elif control is Control.PAUSE:
            await (self.say("Paused. Say continue when you're ready.") if active and await self.tasks.pause() else self.say("Nothing is running right now."))
        elif control is Control.RESUME:
            if active and run.task.paused and await self.tasks.resume():  # type: ignore[union-attr]
                await self.say("Continuing.")
            else:
                await self.say("There's nothing to continue.")
        elif control is Control.STATUS:
            await self.say(self._status_text())
        elif control is Control.PROGRESS:
            await self.say(self._progress_text())
        elif control is Control.REPEAT:
            await self.say(self.last_spoken or "I haven't said anything yet.")
        elif control is Control.BACK:
            res = await self.browser.go_back()
            await self.say("Okay, I went back." if res.success else "There's no earlier page to go back to.")

    async def stop_everything(self, announce: str = "Stopped.") -> None:
        """Voice 'stop' and the STOP button: silence NOOR immediately, cancel the task, return control to the user."""
        await self.voice.interrupt()
        await self.tasks.stop()
        await self.say(announce)

    def _status_text(self) -> str:
        run = self.tasks.run
        if not (self.tasks.active and run):
            return "I'm idle right now." + (f" Last time I worked on: {self.previous_task['goal']}." if self.previous_task else "")
        task = run.task
        if task.paused:
            return "I'm paused. Say continue to resume."
        cur = next((s for s in task.plan if s.status == StepStatus.RUNNING), None)
        if task.status == AgentState.WAITING_FOR_PERMISSION:
            return "I'm waiting for your approval."
        if task.status == AgentState.WAITING_FOR_USER and task.pending_question:
            return f"I'm waiting for you. {task.pending_question}"
        if cur:
            return f"I'm working on your request to {task.goal}. Right now: {cur.description}."
        return f"I'm {task.status.value.lower().replace('_', ' ')} your request to {task.goal}."

    def _progress_text(self) -> str:
        run = self.tasks.run
        if not (self.tasks.active and run and run.task.plan):
            return "I'm not working on anything right now."
        steps = [s for s in run.task.plan if s.status != StepStatus.SKIPPED]
        done = sum(s.status == StepStatus.DONE for s in steps)
        left = len(steps) - done
        return f"I've finished {done} of {len(steps)} steps, so {left} {'step is' if left == 1 else 'steps are'} left."

    async def _permission_answer(self, req: Any, yes: bool) -> None:
        if yes and not req.voice_approvable:
            await self.say("That one needs your approval on screen. Please press Approve if you want me to go ahead.")
            return
        self.permissions.respond(req.id, yes)
        await self.say("Okay." if yes else "Okay, I won't do that.")

    # ---- LLM paths ------------------------------------------------------------------------------------------------------
    async def _context(self, include_browser: bool = True) -> str:
        lines = [f"{'User' if t.role == 'user' else 'NOOR'}: {t.text}" for t in list(self.history)[-8:]]
        parts = ["Recent conversation:\n" + "\n".join(lines)] if lines else []
        if self.previous_task:
            p = self.previous_task
            saved = f" Files created/saved: {', '.join(p['artifacts'])}." if p.get("artifacts") else ""
            parts.append(f"Previous task: {p['goal']} -> {p['status']}. {p.get('result', '')[:300]}{saved}"
                         + (f"\nWhat it read/wrote (excerpt): {p['notes'][:900]}" if p.get("notes") else ""))
        if include_browser and getattr(self.browser, "is_open", False):
            now = time.monotonic()
            if self._cached_snap is None or now - self._cached_snap_time > 5.0:
                try:
                    self._cached_snap = await self.browser.snapshot(900, links=True)
                    self._cached_snap_time = now
                except Exception:
                    self._cached_snap = {}
            snap = self._cached_snap or {}
            if snap.get("url"):
                numbered = "\n".join(f"  {i}. {l['text']}" for i, l in enumerate(snap.get("links", []), 1))
                parts.append(f"Browser currently open: \"{snap.get('title', '')}\" at {snap['url']}\nVisible text (excerpt): {snap.get('text', '')[:600]}"
                             + (f"\nLinks on the page, in order (\"the second one\" = #2):\n{numbered}" if numbered else ""))
        return "\n\n".join(parts)

    async def _classify(self, text: str) -> TurnOut:
        system = (persona(self.prefs.get("response_style", "concise"), self.prefs.get("response_language", "english")) + "\nClassify the user's latest message. new_task: a request to do something on the computer. follow_up_task: a task that "
                  "builds on the conversation or what is on screen ('open the first result', 'download it'). question: asks for information or an "
                  "explanation. smalltalk: greeting/thanks/chat. For task intents write `goal` as a complete standalone instruction with all "
                  "references resolved using the context (e.g. 'Open the first search result for React documentation in the current browser page').")
        return await self.llm.structured(system, f"{await self._context()}\n\nLATEST USER MESSAGE: {text}", TurnOut, retries=1)

    def _obvious_new_task(self, text: str) -> bool:
        """True for an imperative first command with nothing to resolve against (no earlier turns, no previous task, no open page)."""
        if getattr(self.llm, "turns", None):
            return False
        no_context = len(self.history) <= 1 and not self.previous_task and not getattr(self.browser, "is_open", False)
        return no_context and bool(_IMPERATIVE.match(text.strip()))

    async def _start_task(self, goal: str) -> None:
        try:
            if self.metrics:
                await self.metrics.mark_and_emit("task_start")
            await self.tasks.start(goal, context=await self._context())
        except Exception as exc:  # noqa: BLE001
            await self.say(f"Sorry, I couldn't start that. {exc}")

    async def _answer(self, text: str, smalltalk: bool = False) -> None:
        lang = self.prefs.get("response_language", "english").lower()
        system = persona(self.prefs.get("response_style", "concise"), lang)
        user = f"{await self._context(include_browser=not smalltalk)}\n\nUser: {text}\nNOOR:"
        try:
            stream = self.llm.stream_text(system, user)
            if lang == "english":
                await self.say_stream(self._guard_english_stream(stream))
            else:
                await self.say_stream(stream)
        except Exception as exc:  # noqa: BLE001
            log.warning("answer failed: %s", type(exc).__name__)
            await self.say("Sorry, I couldn't come up with an answer.")

    async def _guard_english_stream(self, deltas: AsyncIterator[str]) -> AsyncIterator[str]:
        chunks: list[str] = []
        has_devanagari = False
        async for delta in deltas:
            chunks.append(delta)
            if re.search(r"[\u0900-\u097F]", delta):
                has_devanagari = True
            if not has_devanagari:
                yield delta
        if has_devanagari:
            full_text = "".join(chunks)
            translated = await self._ensure_english(full_text)
            await self.voice.interrupt()
            yield translated

    # ---- task lifecycle hooks ------------------------------------------------------------------------------------------------
    def on_task_finished(self, snapshot: dict[str, Any]) -> None:
        run = self.tasks.run
        self.previous_task = {"goal": snapshot.get("goal", ""), "status": snapshot.get("status", ""), "result": snapshot.get("result", ""),
                              "artifacts": list(run.artifacts)[-5:] if run else [], "notes": "\n".join(run.notes[-2:]) if run else ""}

