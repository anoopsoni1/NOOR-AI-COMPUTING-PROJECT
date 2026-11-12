"""Importing this package registers every tool in REGISTRY."""
from backend.tools import filesystem, interaction, system  # noqa: F401
from backend.tools.base import REGISTRY, ToolContext, ToolRegistry, ToolResult

__all__ = ["REGISTRY", "ToolContext", "ToolRegistry", "ToolResult"]
