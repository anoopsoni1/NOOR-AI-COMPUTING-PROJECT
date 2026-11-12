"""Agent state model: enums, plan/task records, and the mutable TaskRun shared by all graph nodes."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from backend.database.models import new_id, utcnow
from backend.database.repository import run_db
from backend.security.redaction import redact_text, sanitize_args

log = logging.getLogger("nova.agent")


class AgentState(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    WAITING_FOR_PERMISSION = "WAITING_FOR_PERMISSION"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    WAITING_FOR_USER = "WAITING_FOR_USER"


TERMINAL_STATES = {AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED}


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class VerifySpec(BaseModel):
    """Deterministic post-condition for a step. `visual` asks the vision model to judge `expect`."""
    kind: Literal["none", "url_contains", "title_contains", "text_contains", "file_exists", "visual"] = "none"
    value: str = ""


class GenerateSpec(BaseModel):
    """Fill `arg` of the step with LLM-written *text* derived from observations gathered so far."""
    arg: str
    instruction: str


class PlanStep(BaseModel):
    id: int
    tool: str
    description: str
    args: dict[str, Any] = {}
    expect: str = ""
    verify: VerifySpec = VerifySpec()
    generate: Optional[GenerateSpec] = None
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    error: Optional[str] = None


class ActionRecord(BaseModel):
    step_id: int
    tool: str
    args: dict[str, Any] = {}
    success: bool
    message: str = ""
    error_code: Optional[str] = None
    duration_ms: int = 0
    agent_state: str = ""
    ts: datetime = Field(default_factory=utcnow)
    screenshot: Optional[str] = None


class PermissionRequest(BaseModel):
    id: str
    tool: str
    risk: str
    title: str
    details: list[dict[str, str]] = []
    reason: str = ""

    @property
    def voice_approvable(self) -> bool:
        return self.tool not in ("send_email", "run_shell", "install_software")


class LogLine(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    level: Literal["info", "warn", "error"] = "info"
    text: str


class TaskRecord(BaseModel):
    id: str = Field(default_factory=new_id)
    goal: str
    status: AgentState = AgentState.IDLE
    understanding: str = ""
    plan: list[PlanStep] = []
    actions: list[ActionRecord] = []
    errors: list[str] = []
    log: list[LogLine] = []
    pending_question: Optional[str] = None
    pending_permission: Optional[PermissionRequest] = None
    paused: bool = False
    result: str = ""
    replans: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None

    def public(self, for_db: bool = False) -> dict[str, Any]:
        """Serialisable snapshot with secrets/bodies stripped from tool arguments."""
        d = self.model_dump(mode="json")
        for step, raw in zip(d["plan"], self.plan):
            step["args"] = sanitize_args(raw.args)
        for act, raw_act in zip(d["actions"], self.actions):
            act["args"] = sanitize_args(raw_act.args)
        if for_db:
            d["created_at"], d["completed_at"] = self.created_at, self.completed_at
        return d


@dataclass
class Deps:
    """Everything the agent needs; built once in main.py, faked in tests."""
    settings: Any
    llm: Any
    registry: Any
    permissions: Any
    repo: Any
    hub: Any
    browser: Any
    computer: Any
    analyzer: Any
    prefs: dict[str, Any] = field(default_factory=dict)


class TaskCancelled(Exception):
    pass


class TaskRun:
    """Mutable runtime context of one task. Graph nodes receive it via config['configurable']['run']."""

    def __init__(self, task: TaskRecord, deps: Deps) -> None:
        self.task, self.deps = task, deps
        self.cancelled = False
        self.current: dict[str, Any] = {}  # step/tool/args/decision for the action in flight
        self.notes: list[str] = []  # condensed observations used for content generation & summaries
        self.artifacts: set[str] = set()
        self.executions = 0
        self.user_prompts = 0
        self.signatures: Counter[str] = Counter()
        self._answer: asyncio.Future[str] | None = None
        self._persist_lock = asyncio.Lock()

    # ---- state & events -------------------------------------------------------------------
    async def set_state(self, state: AgentState, message: str | None = None) -> None:
        self.task.status = state
        if message:
            self.say(message)
        await self.emit()

    async def speak(self, text: str, chunk: bool = False) -> None:
        speaker = getattr(self.deps, "speaker", None)
        try:
            if speaker is not None:
                await speaker.say(text, chunk=chunk)
            else:
                await self.deps.hub.broadcast({"type": "assistant.message", "text": text})
        except Exception as exc:  # noqa: BLE001 - voice issues must never abort task execution
            log.warning("speak failed: %s", exc)
            try:
                await self.deps.hub.broadcast({"type": "assistant.message", "text": text})
            except Exception:
                pass

    def say(self, text: str, level: Literal["info", "warn", "error"] = "info") -> None:
        text = redact_text(text)
        self.task.log.append(LogLine(text=text, level=level))
        del self.task.log[:-300]
        if level == "error":
            self.task.errors.append(text)

    async def emit(self, persist: bool = True) -> None:
        await self.deps.hub.broadcast({"type": "task", "task": self.task.public()})
        if persist:
            await self.persist()

    async def persist(self) -> None:
        async with self._persist_lock:
            try:
                await run_db(self.deps.repo.upsert_task, self.task.public(for_db=True))
            except Exception:  # noqa: BLE001 - persistence problems must not kill the agent
                log.exception("failed to persist task %s", self.task.id)

    def note(self, text: str, limit: int = 3500) -> None:
        self.notes.append(redact_text(text)[:limit])
        del self.notes[:-8]

    # ---- cancellation / user interaction ------------------------------------------------------
    def check_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled()

    def cancel(self) -> None:
        self.cancelled = True
        if self._answer and not self._answer.done():
            self._answer.cancel()

    async def ask_user(self, question: str) -> str:
        """Pause the loop and wait for the user to respond (login done, missing info, ...)."""
        self.user_prompts += 1
        self.task.pending_question = question
        await self.set_state(AgentState.WAITING_FOR_USER, f"Waiting for you: {question}")
        self._answer = asyncio.get_running_loop().create_future()
        try:
            answer = await asyncio.wait_for(self._answer, self.deps.settings.user_response_timeout_s)
        except asyncio.TimeoutError:
            raise TaskCancelled() from None
        finally:
            self.task.pending_question = None
            self._answer = None
        self.check_cancelled()
        return answer

    def provide_answer(self, text: str) -> bool:
        if self._answer and not self._answer.done():
            self._answer.set_result(text)
            return True
        return False


def new_task(goal: str) -> TaskRecord:
    return TaskRecord(goal=goal.strip())
