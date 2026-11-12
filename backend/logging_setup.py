"""Structured JSON logging with automatic secret redaction."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from backend.security.redaction import redact_obj, redact_text

_EXTRA_FIELDS = ("task_id", "tool", "tool_input", "result", "error", "duration_ms", "agent_state")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        for key in _EXTRA_FIELDS:
            if hasattr(record, key):
                data[key] = redact_obj(getattr(record, key))
        if record.exc_info:
            data["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(data, default=str)


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    fmt = JsonFormatter()
    for handler in (
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(log_dir / "nova.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
    ):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pymongo").setLevel(logging.WARNING)


def log_tool_call(logger: logging.Logger, *, task_id: str, tool: str, tool_input: dict, success: bool,
                  result: str, error: str | None, duration_ms: int, agent_state: str) -> None:
    logger.info(
        "tool_call",
        extra={"task_id": task_id, "tool": tool, "tool_input": tool_input, "result": {"success": success, "message": result},
               "error": error, "duration_ms": duration_ms, "agent_state": agent_state},
    )
