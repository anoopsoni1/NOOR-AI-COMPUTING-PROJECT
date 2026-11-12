"""Permission & safety engine: classifies every tool call as SAFE / CONFIRM / BLOCKED and runs approvals."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic import BaseModel

from backend.database.repository import run_db
from backend.security.redaction import preview, sanitize_args
from backend.tools.filesystem import inside_home, is_executable_name, is_sensitive_path, is_system_path, resolve_path

if TYPE_CHECKING:
    from backend.agent.state import TaskRun

log = logging.getLogger("nova.permissions")


class Risk(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    BLOCKED = "blocked"


class Decision(BaseModel):
    risk: Risk
    reason: str = ""
    title: str = ""
    details: list[dict[str, str]] = []


# --- patterns that are blocked by default -------------------------------------------------------------------------
_FINANCIAL = re.compile(r"\b(checkout|place (your )?order|buy now|pay now|complete (purchase|payment)|confirm (purchase|payment)|"
                        r"wire transfer|send money|add (a )?(credit |debit )?card|payment method|purchase)\b", re.I)
_FINANCIAL_URL = re.compile(r"/(checkout|payment|payments|billing|pay|cart/confirm)(/|$|\?)", re.I)
_PASSWORD_CHANGE = re.compile(r"(change|reset|update|new|forgot) (my |your |the )?password|two[- ]factor|2fa|api key|secret key|private key|"
                              r"delete (my |your )?account|revoke (access|token)|security (settings|key)", re.I)
_CREDENTIAL_FIELD = re.compile(r"pass(word|code)|\bpin\b|cvv|cvc|card number|security code|ssn|social security|otp|one[- ]time", re.I)
_DESTRUCTIVE_CMD = re.compile(r"(^|[\s;&|])(rm|del|erase|rmdir|rd|format|diskpart|shutdown|mkfs|dd|reg(\.exe)?\s+delete|"
                              r"remove-item|clear-content|cipher|takeown|icacls|net\s+user|schtasks|sc\s+delete)(\s|$)", re.I)



CAPABILITY_MODES = ("ENABLED", "ASK", "DISABLED")

CAPABILITIES: dict[str, tuple[str, str]] = {
    "MICROPHONE": ("Microphone", "Listen for the wake word and speech input"),
    "BROWSER": ("Web browser", "Open websites, read pages, fill forms and automate browsing"),
    "FILESYSTEM": ("File system", "Read, write, create, move and organize files in your home directory"),
    "DESKTOP": ("Desktop control", "Control the mouse and keyboard outside the browser, take screenshots"),
    "SYSTEM": ("System & commands", "Run shell commands, launch applications, install packages"),
    "EMAIL": ("Email & communication", "Compose and send emails"),
}

DEFAULT_CAPABILITY_MODE: dict[str, str] = {
    "MICROPHONE": "ENABLED",
    "BROWSER": "ENABLED",
    "FILESYSTEM": "ENABLED",
    "DESKTOP": "ASK",
    "SYSTEM": "ENABLED",
    "EMAIL": "ASK",
}


def capabilities_for(tool: str, args: dict[str, Any] | None = None) -> list[str]:
    a = args or {}
    mode = a.get("mode", "browser")
    if tool in ("open_url", "read_page", "find_element", "scroll", "wait", "download_file"):
        return ["BROWSER"]
    if tool in ("click", "type_text", "press_key"):
        return ["DESKTOP"] if mode == "desktop" else ["BROWSER"]
    if tool in ("read_file", "create_file", "move_file", "move_files", "list_directory", "make_directory", "upload_file"):
        return ["FILESYSTEM"]
    if tool in ("take_screenshot",):
        return ["DESKTOP"]
    if tool in ("run_shell", "open_application", "install_software", "set_brightness"):
        return ["SYSTEM"]
    if tool == "send_email":
        return ["EMAIL"]
    return []


class PermissionEngine:
    def __init__(self, settings: Any, repo: Any) -> None:
        self.settings, self.repo = settings, repo
        self.rules: dict[str, str] = {}  # tool -> "ask" | "allow"  (user overrides, never lifts BLOCKED)
        self.capabilities: dict[str, str] = {}
        self._pending: dict[str, tuple[asyncio.Future[bool], str]] = {}

    async def load_rules(self) -> None:
        if self.repo:
            self.rules = await run_db(self.repo.list_rules)
            if hasattr(self.repo, "list_capabilities"):
                self.capabilities = await run_db(self.repo.list_capabilities)

    def capability_mode(self, cap: str) -> str:
        return self.capabilities.get(cap, DEFAULT_CAPABILITY_MODE.get(cap, "ENABLED"))

    def capability_enabled(self, cap: str) -> bool:
        return self.capability_mode(cap) != "DISABLED"

    # ---- classification -------------------------------------------------------------------------------------------
    def decide(self, tool: str, args: dict[str, Any], page_url: str = "") -> Decision:
        title, details = describe(tool, args)
        d = self._classify(tool, args, page_url)
        d.title, d.details = title, details
        if d.risk is Risk.BLOCKED:
            return d

        for cap in capabilities_for(tool, args):
            if not self.capability_enabled(cap):
                return Decision(risk=Risk.BLOCKED, reason=f"The '{cap}' capability is disabled in Permissions", title=title, details=details)
            if self.capability_mode(cap) == "ASK" and d.risk is Risk.SAFE:
                d = d.model_copy(update={"risk": Risk.CONFIRM, "reason": f"Confirmation required for '{cap}' capability"})

        policy = self.rules.get(tool)
        if policy == "ask" and d.risk is Risk.SAFE:
            return d.model_copy(update={"risk": Risk.CONFIRM, "reason": "You asked to be prompted for this tool"})
        if policy == "allow" and d.risk is Risk.CONFIRM:
            return d.model_copy(update={"risk": Risk.SAFE, "reason": "Auto-approved by your permission rule"})
        return d

    def _classify(self, tool: str, a: dict[str, Any], page_url: str) -> Decision:
        S, C, B = Risk.SAFE, Risk.CONFIRM, Risk.BLOCKED
        mode = a.get("mode", "browser")
        label = " ".join(str(a.get(k, "")) for k in ("target", "description", "selector", "url", "command"))

        # Content-based blocks apply to every tool.
        if tool in ("click", "type_text", "find_element", "open_url", "download_file"):
            if _FINANCIAL.search(label) or (tool == "open_url" and _FINANCIAL_URL.search(urlparse(str(a.get('url', ''))).path + "/")):
                return Decision(risk=B, reason="Financial transactions are blocked by default")
            if _PASSWORD_CHANGE.search(label):
                return Decision(risk=B, reason="Password / security-sensitive changes are blocked by default")
            if tool == "type_text" and _CREDENTIAL_FIELD.search(label):
                return Decision(risk=B, reason="Typing credentials is blocked — enter them yourself")
            if tool == "click" and _FINANCIAL_URL.search(urlparse(page_url).path + "/") and re.search(r"\b(pay|order|buy|confirm|submit)\b", label, re.I):
                return Decision(risk=B, reason="Financial transactions are blocked by default")

        if tool == "open_url":
            scheme = urlparse(str(a.get("url", ""))).scheme
            return Decision(risk=B, reason=f"URL scheme '{scheme}' is not allowed in open_url (use open_application for system pages)") if scheme and scheme not in ("http", "https") else Decision(risk=S)
        if tool == "download_file":
            return Decision(risk=B, reason="Only http(s) downloads are allowed") if a.get("url") and not str(a["url"]).lower().startswith(("http://", "https://")) else Decision(risk=S)
        if tool in ("click", "type_text", "press_key") and mode == "desktop":
            return Decision(risk=C, reason="Controls your mouse/keyboard outside the browser")
        if tool in ("click", "type_text", "press_key", "scroll", "wait", "find_element", "read_page", "take_screenshot", "ask_user", "set_brightness"):
            return Decision(risk=S)

        if tool in ("read_file", "list_directory"):
            p = resolve_path(str(a.get("path", "")))
            if is_sensitive_path(p):
                return Decision(risk=B, reason=f"{p.name} may contain credentials/secrets")
            if is_system_path(p) or not inside_home(p):
                return Decision(risk=C, reason="Location is outside your home folder")
            return Decision(risk=S)
        if tool in ("create_file", "make_directory"):
            p = resolve_path(str(a.get("path", "")))
            if is_sensitive_path(p) or is_system_path(p):
                return Decision(risk=B, reason="Writing to system or credential locations is blocked")
            if tool == "create_file" and is_executable_name(p):
                return Decision(risk=C, reason="Executable file creation requires approval")

            if not inside_home(p):
                return Decision(risk=C, reason="Location is outside your home folder")
            return Decision(risk=S)
        if tool in ("move_file", "move_files"):
            moves = a.get("moves") or [{"source": a.get("source", ""), "destination": a.get("destination", "")}]
            for mv in moves:
                for key in ("source", "destination"):
                    p = resolve_path(str(mv.get(key, "")))
                    if is_sensitive_path(p) or is_system_path(p):
                        return Decision(risk=B, reason=f"Moving files to/from protected location ({p.name})")
            return Decision(risk=C, reason="Moving files changes your file system")
        if tool == "upload_file":
            p = resolve_path(str(a.get("path", "")))
            if is_sensitive_path(p):
                return Decision(risk=B, reason="Uploading credential files is blocked")
            return Decision(risk=C, reason="Uploading sends a local file to a website")
        if tool == "run_shell":
            if _DESTRUCTIVE_CMD.search(str(a.get("command", ""))):
                return Decision(risk=B, reason="Destructive / system-level commands are blocked by default")
            return Decision(risk=C, reason="Shell commands can change your system")
        if tool == "install_software":
            return Decision(risk=C, reason="Installing software changes your system")
        if tool == "open_application":
            return Decision(risk=S)
        if tool == "send_email":
            return Decision(risk=C, reason="Requires your approval")
        return Decision(risk=B, reason=f"Unknown tool '{tool}'")

    # ---- approval workflow -----------------------------------------------------------------------------------------
    async def request_approval(self, run: "TaskRun", tool: str, args: dict[str, Any], decision: Decision) -> bool:
        from backend.agent.state import AgentState, PermissionRequest

        req = PermissionRequest(id=uuid.uuid4().hex, tool=tool, risk=decision.risk.value, title=decision.title,
                                details=decision.details, reason=decision.reason)
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[req.id] = (fut, run.task.id)
        run.task.pending_permission = req
        await run.speak(f"Approval needed: {decision.title}")
        await run.set_state(AgentState.WAITING_FOR_PERMISSION, f"Approval needed: {decision.title}")
        try:
            approved = await asyncio.wait_for(fut, self.settings.approval_timeout_s)
        except asyncio.TimeoutError:
            approved = False
            run.say("Approval request timed out; action denied", "warn")
        except asyncio.CancelledError:
            approved = False
        finally:
            self._pending.pop(req.id, None)
            run.task.pending_permission = None
        await self._audit(run.task.id, tool, args, decision, "approved" if approved else "denied")
        run.check_cancelled()
        return approved

    def respond(self, request_id: str, approve: bool) -> bool:
        entry = self._pending.get(request_id)
        if not entry or entry[0].done():
            return False
        entry[0].set_result(approve)
        return True

    def cancel_task(self, task_id: str) -> None:
        for fut, tid in list(self._pending.values()):
            if tid == task_id and not fut.done():
                fut.cancel()

    async def audit_blocked(self, task_id: str, tool: str, args: dict[str, Any], decision: Decision) -> None:
        await self._audit(task_id, tool, args, decision, "blocked")

    async def _audit(self, task_id: str, tool: str, args: dict[str, Any], decision: Decision, outcome: str) -> None:
        try:
            await run_db(self.repo.add_audit, {"taskId": task_id, "tool": tool, "args": sanitize_args(args), "risk": decision.risk.value,
                                                "title": decision.title, "reason": decision.reason, "outcome": outcome})
        except Exception:  # noqa: BLE001
            log.exception("audit write failed")


def describe(tool: str, a: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Human readable 'NOVA wants to: ...' text. Never includes secrets (bodies are previewed + redacted)."""
    def row(k: str, v: Any) -> dict[str, str]:
        return {"label": k, "value": preview(str(v), 300)}

    if tool == "send_email":
        return "Send an email", [row("To", a.get("to", "")), row("Subject", a.get("subject", "")), row("Body", a.get("body", ""))]
    if tool == "upload_file":
        return "Upload a file", [row("File", a.get("path", "")), row("Field", a.get("target", "file input"))]
    if tool == "move_file":
        return "Move a file", [row("From", a.get("source", "")), row("To", a.get("destination", ""))]
    if tool == "move_files":
        moves = a.get("moves", [])
        shown = [row(f"{i + 1}", f"{m.get('source', '')} → {m.get('destination', '')}") for i, m in enumerate(moves[:12])]
        if len(moves) > 12:
            shown.append(row("…", f"and {len(moves) - 12} more"))
        return f"Move {len(moves)} file(s)", shown
    if tool == "run_shell":
        return "Run a shell command", [row("Command", a.get("command", "")), row("Folder", a.get("cwd") or "(default)")]
    if tool == "install_software":
        return "Install software", [row("Manager", a.get("manager", "")), row("Package", a.get("package", ""))]
    if tool == "open_application":
        return "Open an application", [row("Application", a.get("name", ""))]
    if tool == "create_file":
        return "Create a file", [row("Path", a.get("path", "")), row("Size", f"{len(str(a.get('content', '')))} chars"), row("Overwrite", a.get("overwrite", False))]
    if tool in ("click", "type_text", "press_key", "scroll") and a.get("mode") == "desktop":
        what = {"click": f"Click '{a.get('target') or (a.get('x'), a.get('y'))}' on your screen", "type_text": f"Type {len(str(a.get('text', '')))} characters on your keyboard",
                "press_key": f"Press {a.get('key', '')}", "scroll": "Scroll"}[tool]
        return what, [row("Surface", "Desktop (outside the browser)")]
    return f"Use {tool}", [row(k, v) for k, v in sanitize_args(a).items()][:6]
