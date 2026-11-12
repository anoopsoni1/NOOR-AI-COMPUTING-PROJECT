"""Typed tool framework: every computer action is a registered Tool returning a ToolResult."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from backend.config import Settings
    from backend.tools.browser import BrowserManager
    from backend.tools.computer import Computer
    from backend.vision.screen_analyzer import ScreenAnalyzer


class ToolResult(BaseModel):
    success: bool
    action: str
    target: str = ""
    message: str = ""
    error_code: str | None = None  # element_not_found, login_required, timeout, network_error, permission_denied, ...
    data: dict[str, Any] = {}

    @classmethod
    def ok(cls, action: str, target: str = "", message: str = "", **data: Any) -> "ToolResult":
        return cls(success=True, action=action, target=target, message=message or f"{action} succeeded", data=data)

    @classmethod
    def fail(cls, action: str, message: str, code: str = "failed", target: str = "", **data: Any) -> "ToolResult":
        return cls(success=False, action=action, target=target, message=message, error_code=code, data=data)


@dataclass
class ToolContext:
    task_id: str
    settings: "Settings"
    browser: "BrowserManager"
    computer: "Computer"
    analyzer: "ScreenAnalyzer | None" = None
    extra: dict[str, Any] = field(default_factory=dict)

    def screenshot_path(self, label: str = "shot") -> Path:
        d = self.settings.screenshots_dir / self.task_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{int(time.time() * 1000)}_{label}.png"


Handler = Callable[[ToolContext, Any], Awaitable[ToolResult]]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    category: str  # browser | desktop | filesystem | system | meta

    def signature(self) -> str:
        props = self.args_model.model_json_schema().get("properties", {})
        required = set(self.args_model.model_json_schema().get("required", []))
        parts = []
        for name, spec in props.items():
            typ = spec.get("type") or ("enum:" + "|".join(map(str, spec["enum"])) if "enum" in spec else "any")
            if "anyOf" in spec:
                typ = "|".join(str(s.get("type", "any")) for s in spec["anyOf"] if s.get("type") != "null")
            desc = f" — {spec['description']}" if spec.get("description") else ""
            parts.append(f"{name}{'' if name in required else '?'}: {typ}{desc}")
        return f"{self.name}({'; '.join(parts)}): {self.description}"


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> str:
        return "\n".join(f"- {t.signature()}" for t in self._tools.values())

    def validate_args(self, name: str, args: dict[str, Any]) -> BaseModel:
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"unknown tool '{name}'")
        try:
            return tool.args_model.model_validate(args)
        except ValidationError as exc:
            msgs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise ValueError(f"invalid args for {name}: {msgs}") from None

    async def run(self, ctx: ToolContext, name: str, args: BaseModel) -> ToolResult:
        tool = self._tools[name]
        try:
            return await tool.handler(ctx, args)
        except Exception as exc:  # noqa: BLE001 - tool layer must never crash the agent loop
            closed = "has been closed" in str(exc) or type(exc).__name__ == "TargetClosedError"
            return ToolResult.fail(name, f"{type(exc).__name__}: {str(exc)[:300]}", code="browser_closed" if closed else "exception")


REGISTRY = ToolRegistry()


def tool(name: str, description: str, args_model: type[BaseModel], category: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        REGISTRY.register(Tool(name, description, args_model, fn, category))
        return fn

    return deco
