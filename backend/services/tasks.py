"""TaskManager: owns the single active task, runs the LangGraph loop, and implements the emergency stop."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.agent.graph import GRAPH
from backend.agent.state import AgentState, Deps, StepStatus, TaskCancelled, TaskRun, TERMINAL_STATES, new_task
from backend.database.models import utcnow
from backend.database.repository import run_db
from backend.llm import LLMError
from backend.services import memory
from backend.services.prefs import effective

log = logging.getLogger("nova.tasks")


class TaskBusy(RuntimeError):
    pass


class TaskManager:
    def __init__(self, deps: Deps) -> None:
        self.deps = deps
        self.run: TaskRun | None = None
        self._job: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self._job is not None and not self._job.done()

    def snapshot(self) -> dict[str, Any] | None:
        return self.run.task.public() if self.run else None

    async def start(self, goal: str, context: str = "") -> str:
        if self.active:
            raise TaskBusy("A task is already running. Stop it first.")
        if not self.deps.llm.configured:
            raise ValueError("LLM is not configured. Set LLM_API_KEY and LLM_MODEL in .env and restart the backend.")
        stored = await run_db(self.deps.repo.get_preferences)
        self.deps.prefs.clear()
        self.deps.prefs.update(effective(self.deps.settings, stored))
        self.deps.browser.headless = self.deps.prefs["browser_headless"]
        self.deps.computer.resume()
        self.run = TaskRun(new_task(goal), self.deps)
        self.run.context = context
        self._job = asyncio.create_task(self._execute(self.run), name=f"noor-task-{self.run.task.id}")
        return self.run.task.id

    async def _execute(self, run: TaskRun) -> None:
        try:
            await run.emit()
            await GRAPH.ainvoke({"route": ""}, config={"configurable": {"run": run}, "recursion_limit": 600})
        except (TaskCancelled, asyncio.CancelledError):
            await self._finish_cancelled(run)
            return
        except LLMError as exc:
            await self._finish_failed(run, f"AI model error: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("task crashed")
            await self._finish_failed(run, f"Unexpected error: {type(exc).__name__}: {str(exc)[:200]}")
        if run.task.status == AgentState.COMPLETED:
            try:
                n = await memory.extract_and_store(run)
                if n:
                    run.say(f"Remembered {n} thing(s) for next time")
                    await run.emit()
            except Exception:  # noqa: BLE001
                log.exception("memory extraction failed")
        await self.deps.hub.broadcast({"type": "finished", "taskId": run.task.id, "status": run.task.status.value})

    async def _finish_failed(self, run: TaskRun, reason: str) -> None:
        run.task.result, run.task.completed_at = reason, utcnow()
        run.say(reason, "error")
        await run.set_state(AgentState.FAILED)

    async def _finish_cancelled(self, run: TaskRun) -> None:
        run.task.completed_at = utcnow()
        run.task.pending_question, run.task.pending_permission = None, None
        run.task.result = "Stopped by you."
        for s in run.task.plan:
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                s.status = StepStatus.SKIPPED
        await run.set_state(AgentState.CANCELLED, "Task stopped — control returned to you")
        await self.deps.hub.broadcast({"type": "finished", "taskId": run.task.id, "status": "CANCELLED"})

    async def stop(self) -> bool:
        """EMERGENCY STOP: cancel the loop, deny pending approvals, halt desktop input, close the browser."""
        run = self.run
        if not run or run.task.status in TERMINAL_STATES or not self.active:
            return False
        run.cancel()
        self.deps.permissions.cancel_task(run.task.id)
        self.deps.computer.halt()
        if self._job:
            self._job.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(self._job), 10) if self._job else None
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        await self.deps.browser.close()
        return True

    async def pause(self) -> bool:
        if self.run and not self.run.task.paused:
            self.run.task.paused = True
            await self.run.emit()
            return True
        return False

    async def resume(self) -> bool:
        if self.run and self.run.task.paused:
            self.run.task.paused = False
            await self.run.emit()
            return True
        return False

    def respond(self, text: str) -> bool:
        return bool(self.run and self.run.provide_answer(text))
