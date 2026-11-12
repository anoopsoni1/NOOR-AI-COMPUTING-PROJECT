"""Lightweight long-term memory: user-managed facts plus conservative auto-extraction after successful tasks."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from backend.agent.state import TaskRun
from backend.database.models import MemoryDoc
from backend.database.repository import run_db
from backend.security.redaction import looks_sensitive

log = logging.getLogger("nova.memory")


class MemoryItem(BaseModel):
    content: str
    category: Literal["preference", "app", "folder", "workflow", "outcome", "other"] = "other"


class MemoryOut(BaseModel):
    memories: list[MemoryItem] = []


async def extract_and_store(run: TaskRun) -> int:
    """Ask the LLM for at most 2 durable, non-sensitive facts worth remembering. Sensitive-looking ones are dropped."""
    deps = run.deps
    if not (deps.prefs.get("auto_memory", True) and deps.llm.configured):
        return 0
    steps = "\n".join(f"- {s.tool}: {s.description}" for s in run.task.plan if s.status.value == "done")
    system = ("Extract at most 2 durable facts about the USER's habits or preferences from this completed task that would help future tasks "
              "(preferred folders, frequently used apps/sites, naming habits, reusable workflows). Do NOT include passwords, keys, tokens, "
              "personal identifiers, financial data, page contents or one-off details. Return an empty list if nothing qualifies. "
              "Write each as a short third-person statement, e.g. 'The user usually saves project files in Projects/'.")
    try:
        out = await deps.llm.structured(system, f"GOAL: {run.task.goal}\nDONE STEPS:\n{steps}", MemoryOut, retries=0)
    except Exception as exc:  # noqa: BLE001
        log.info("memory extraction skipped: %s", type(exc).__name__)
        return 0
    existing = {m["content"].lower() for m in await run_db(deps.repo.list_memories)}
    stored = 0
    for item in out.memories[:2]:
        text = item.content.strip()
        if not text or len(text) > 240 or looks_sensitive(text) or text.lower() in existing:
            continue
        await run_db(deps.repo.add_memory, MemoryDoc(content=text, category=item.category, source="auto"))
        stored += 1
    return stored
