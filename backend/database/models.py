"""MongoDB collection names and document models."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

USERS = "users"
TASKS = "tasks"
TASK_EXECUTIONS = "taskExecutions"
PERMISSIONS = "permissions"
SESSIONS = "sessions"
MEMORIES = "memories"

LOCAL_USER_ID = "local"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class MemoryDoc(BaseModel):
    id: str = Field(default_factory=new_id)
    content: str
    category: Literal["preference", "app", "folder", "workflow", "outcome", "other"] = "other"
    source: Literal["user", "auto"] = "user"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def to_doc(self) -> dict[str, Any]:
        d = self.model_dump()
        d["_id"] = d.pop("id")
        d["userId"] = LOCAL_USER_ID
        return d


class PermissionRule(BaseModel):
    tool: str
    policy: Literal["default", "ask", "allow"] = "default"
