"""Secret redaction helpers used by logging and persistence."""
from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"pass(word|wd|code)?$|secret|token|api[_-]?key|authorization|cookie|credential|otp|cvv|ssn", re.I)
_CONTENT_KEYS = {"content", "body", "text"}

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_KEY]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
]

# Used by the memory service to refuse storing sensitive facts.
_SENSITIVE_MEMORY = re.compile(
    r"password|passwd|passcode|secret|api[_ -]?key|token|credential|ssn|social security|credit card|card number|cvv|iban|pin code|otp",
    re.I,
)


def redact_text(value: str) -> str:
    for pattern, repl in _PATTERNS:
        value = pattern.sub(repl, value)
    return value


def redact_obj(obj: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else redact_obj(v, depth + 1)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, depth + 1) for v in obj[:100]]
    return obj


def sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Copy of tool arguments that is safe to log/persist: bodies are reduced to their length."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key in _CONTENT_KEYS and isinstance(value, str):
            out[key] = f"<{len(value)} chars>"
        else:
            out[key] = redact_obj({key: value})[key]
    return out


def looks_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_MEMORY.search(text)) or redact_text(text) != text


def preview(text: str, limit: int = 200) -> str:
    text = redact_text(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
