"""Backward-compatibility module; all LLM provider logic is in backend.llm."""
from __future__ import annotations

from backend.llm import LLMClient, LLMError, LLMUnreachable, compact_schema, extract_json

__all__ = ["LLMClient", "LLMError", "LLMUnreachable", "compact_schema", "extract_json"]
