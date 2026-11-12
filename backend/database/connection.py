"""MongoDB connection + index management."""
from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from backend.config import Settings
from backend.database import models as m

log = logging.getLogger("nova.db")

_client: Any = None
_db: Database | None = None
in_memory = False


def connect(settings: Settings) -> Database:
    """Connect to MongoDB (falling back to an in-memory mongomock database in dev)."""
    global _client, _db, in_memory
    if _db is not None:
        return _db
    try:
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        _client, _db = client, client[settings.database_name]
        log.info("Connected to MongoDB database '%s'", settings.database_name)
    except Exception as exc:  # noqa: BLE001
        if not settings.allow_in_memory_db:
            raise RuntimeError(f"MongoDB unreachable at {settings.mongodb_uri}: {exc}") from exc
        try:
            import mongomock
        except ImportError as imp:
            raise RuntimeError("MongoDB unreachable and mongomock not installed") from imp
        log.warning("MongoDB unreachable (%s); using NON-PERSISTENT in-memory database", exc)
        _client = mongomock.MongoClient()
        _db = _client[settings.database_name]
        in_memory = True
    ensure_indexes(_db, settings)
    return _db


def ensure_indexes(db: Database, settings: Settings) -> None:
    db[m.USERS].create_index([("userId", ASCENDING)], unique=True)
    db[m.TASKS].create_index([("created_at", DESCENDING)])
    db[m.TASKS].create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    # Executions expire automatically with the task retention window.
    db[m.TASK_EXECUTIONS].create_index([("taskId", ASCENDING), ("ts", ASCENDING)])
    db[m.TASK_EXECUTIONS].create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    db[m.PERMISSIONS].create_index([("kind", ASCENDING), ("tool", ASCENDING)])
    db[m.PERMISSIONS].create_index([("ts", DESCENDING)])
    db[m.SESSIONS].create_index([("expiresAt", ASCENDING)], expireAfterSeconds=0)
    db[m.MEMORIES].create_index([("userId", ASCENDING), ("updated_at", DESCENDING)])


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialised")
    return _db


def close() -> None:
    global _client, _db
    if _client is not None:
        try:
            _client.close()
        finally:
            _client, _db = None, None
