"""Synchronous data access layer. Call through `run_db` from async code."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Callable, TypeVar

from pymongo.database import Database

from backend.database import models as m

T = TypeVar("T")


async def run_db(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    return await asyncio.to_thread(fn, *args, **kwargs)


def serialize(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Mongo document -> JSON friendly dict."""
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cached_prefs: dict[str, Any] | None = None
        self._cached_prefs_time: float = 0.0

    # ---- users / preferences --------------------------------------------------
    def ensure_user(self) -> None:
        self.db[m.USERS].update_one(
            {"userId": m.LOCAL_USER_ID},
            {"$setOnInsert": {"userId": m.LOCAL_USER_ID, "preferences": {}, "created_at": m.utcnow()}},
            upsert=True,
        )

    def get_preferences(self) -> dict[str, Any]:
        import time
        now = time.monotonic()
        if self._cached_prefs is not None and (now - self._cached_prefs_time) < 30.0:
            return dict(self._cached_prefs)
        doc = self.db[m.USERS].find_one({"userId": m.LOCAL_USER_ID}) or {}
        prefs = doc.get("preferences", {})
        self._cached_prefs = prefs
        self._cached_prefs_time = now
        return dict(prefs)

    def set_preferences(self, prefs: dict[str, Any]) -> dict[str, Any]:
        import time
        merged = {**self.get_preferences(), **prefs}
        self.db[m.USERS].update_one({"userId": m.LOCAL_USER_ID}, {"$set": {"preferences": merged}}, upsert=True)
        self._cached_prefs = merged
        self._cached_prefs_time = time.monotonic()
        return merged

    # ---- tasks ----------------------------------------------------------------
    def upsert_task(self, task: dict[str, Any]) -> None:
        doc = dict(task)
        task_id = doc.pop("id")
        self.db[m.TASKS].replace_one({"_id": task_id}, {**doc, "_id": task_id, "userId": m.LOCAL_USER_ID}, upsert=True)

    def list_tasks(self, limit: int = 50, skip: int = 0, status: str | None = None) -> list[dict[str, Any]]:
        query = {"status": status} if status else {}
        proj = {"goal": 1, "status": 1, "created_at": 1, "completed_at": 1, "result": 1, "replans": 1, "errors": 1}
        cur = self.db[m.TASKS].find(query, proj).sort("created_at", -1).skip(skip).limit(limit)
        return [serialize(d) for d in cur]  # type: ignore[misc]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return serialize(self.db[m.TASKS].find_one({"_id": task_id}))

    def delete_task(self, task_id: str) -> bool:
        self.db[m.TASK_EXECUTIONS].delete_many({"taskId": task_id})
        return self.db[m.TASKS].delete_one({"_id": task_id}).deleted_count > 0

    def stats(self) -> dict[str, Any]:
        counts = {d["_id"]: d["n"] for d in self.db[m.TASKS].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
        return {"total": sum(counts.values()), "byStatus": counts}

    def today_stats(self) -> dict[str, Any]:
        start = m.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        counts = {d["_id"]: d["n"] for d in self.db[m.TASKS].aggregate([
            {"$match": {"createdAt": {"$gte": start}}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}}
        ])}
        return {"total": sum(counts.values()), "byStatus": counts}

    def purge_old_tasks(self, days: int) -> int:
        cutoff = m.utcnow() - timedelta(days=days)
        old = [d["_id"] for d in self.db[m.TASKS].find({"created_at": {"$lt": cutoff}}, {"_id": 1})]
        for tid in old:
            self.delete_task(tid)
        return len(old)

    # ---- executions -------------------------------------------------------------
    def add_execution(self, doc: dict[str, Any], retention_days: int) -> None:
        doc = {**doc, "ts": m.utcnow(), "expiresAt": m.utcnow() + timedelta(days=retention_days)}
        self.db[m.TASK_EXECUTIONS].insert_one(doc)

    def list_executions(self, task_id: str) -> list[dict[str, Any]]:
        cur = self.db[m.TASK_EXECUTIONS].find({"taskId": task_id}).sort("ts", 1)
        return [serialize(d) for d in cur]  # type: ignore[misc]

    # ---- permissions --------------------------------------------------------------
    def list_rules(self) -> dict[str, str]:
        return {d["tool"]: d["policy"] for d in self.db[m.PERMISSIONS].find({"kind": "rule"})}

    def set_rule(self, tool: str, policy: str) -> None:
        if policy == "default":
            self.db[m.PERMISSIONS].delete_one({"kind": "rule", "tool": tool})
        else:
            self.db[m.PERMISSIONS].update_one(
                {"kind": "rule", "tool": tool}, {"$set": {"policy": policy, "updated_at": m.utcnow()}}, upsert=True
            )

    def list_capabilities(self) -> dict[str, str]:
        return {d["capability"]: d["mode"] for d in self.db[m.PERMISSIONS].find({"kind": "capability"})}

    def set_capability(self, capability: str, mode: str) -> None:
        self.db[m.PERMISSIONS].update_one(
            {"kind": "capability", "capability": capability},
            {"$set": {"mode": mode, "updated_at": m.utcnow()}},
            upsert=True,
        )

    def add_audit(self, doc: dict[str, Any]) -> None:
        self.db[m.PERMISSIONS].insert_one({**doc, "kind": "audit", "ts": m.utcnow()})

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = self.db[m.PERMISSIONS].find({"kind": "audit"}).sort("ts", -1).limit(limit)
        return [serialize(d) for d in cur]  # type: ignore[misc]

    # ---- sessions -------------------------------------------------------------------
    def create_session(self, ttl_hours: int = 24) -> str:
        sid = m.new_id()
        self.db[m.SESSIONS].insert_one(
            {"_id": sid, "userId": m.LOCAL_USER_ID, "started_at": m.utcnow(), "expiresAt": m.utcnow() + timedelta(hours=ttl_hours)}
        )
        return sid

    # ---- memories -----------------------------------------------------------------------
    def list_memories(self, limit: int = 200) -> list[dict[str, Any]]:
        cur = self.db[m.MEMORIES].find({"userId": m.LOCAL_USER_ID}).sort("updated_at", -1).limit(limit)
        return [serialize(d) for d in cur]  # type: ignore[misc]

    def add_memory(self, mem: m.MemoryDoc) -> dict[str, Any]:
        self.db[m.MEMORIES].insert_one(mem.to_doc())
        return serialize(self.db[m.MEMORIES].find_one({"_id": mem.id}))  # type: ignore[return-value]

    def update_memory(self, memory_id: str, content: str, category: str | None) -> dict[str, Any] | None:
        upd: dict[str, Any] = {"content": content, "updated_at": m.utcnow()}
        if category:
            upd["category"] = category
        self.db[m.MEMORIES].update_one({"_id": memory_id}, {"$set": upd})
        return serialize(self.db[m.MEMORIES].find_one({"_id": memory_id}))

    def delete_memory(self, memory_id: str) -> bool:
        return self.db[m.MEMORIES].delete_one({"_id": memory_id}).deleted_count > 0
