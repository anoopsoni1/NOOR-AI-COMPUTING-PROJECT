"""System-level tools. All of these are CONFIRMATION-gated by the permission engine."""
from __future__ import annotations

import asyncio
import os
import shlex
import smtplib
import subprocess
import sys
from email.message import EmailMessage

from pydantic import BaseModel, Field

from backend.security.redaction import redact_text
from backend.tools.base import ToolContext, ToolResult, tool


class RunShellArgs(BaseModel):
    command: str = Field(description="single command line (no pipes/redirection); executed without a shell")
    cwd: str = ""
    timeout_s: int = Field(30, ge=1, le=300)


@tool("run_shell", "Run a command (needs user approval; destructive commands are blocked)", RunShellArgs, "system")
async def run_shell(ctx: ToolContext, a: RunShellArgs) -> ToolResult:
    argv = shlex.split(a.command, posix=sys.platform != "win32")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=a.cwd or None, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=a.timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        return ToolResult.fail("run_shell", f"Command timed out after {a.timeout_s}s", "timeout", target=argv[0])
    except FileNotFoundError:
        return ToolResult.fail("run_shell", f"Command not found: {argv[0]}", "not_found", target=argv[0])
    text = redact_text(out.decode(errors="replace"))[:4000]
    return ToolResult(success=proc.returncode == 0, action="run_shell", target=argv[0],
                      message=f"Exit code {proc.returncode}", error_code=None if proc.returncode == 0 else "nonzero_exit",
                      data={"output": text, "exit_code": proc.returncode})


class SetBrightnessArgs(BaseModel):
    level: int | None = Field(None, ge=0, le=100, description="absolute brightness percent, e.g. 60")
    change: int | None = Field(None, ge=-100, le=100, description="relative change in percent, e.g. -20 to dim, +20 to brighten")


_BRIGHTNESS_PS = (
    "$ErrorActionPreference='Stop'; "
    "$cur = [int](Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness | Select-Object -First 1).CurrentBrightness; "
    "if ($env:BR_LEVEL -ne '') { $t = [int]$env:BR_LEVEL } elseif ($env:BR_CHANGE -ne '') { $t = $cur + [int]$env:BR_CHANGE } else { $t = $cur }; "
    "$t = [Math]::Max(0, [Math]::Min(100, $t)); "
    "if ($t -ne $cur) { Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=[byte]$t} | Out-Null }; "
    "Write-Output \"$cur $t\""
)


@tool("set_brightness", "Set or adjust the built-in display brightness (Windows laptops; give level OR change)", SetBrightnessArgs, "system")
async def set_brightness(ctx: ToolContext, a: SetBrightnessArgs) -> ToolResult:
    if a.level is None and a.change is None:
        return ToolResult.fail("set_brightness", "Give a level (0-100) or a change (e.g. -20)", "invalid_args")
    if sys.platform != "win32":
        return ToolResult.fail("set_brightness", "Brightness control is only implemented for Windows", "not_supported")
    env = {**os.environ, "BR_LEVEL": "" if a.level is None else str(a.level), "BR_CHANGE": "" if a.change is None else str(a.change)}
    try:
        proc = await asyncio.create_subprocess_exec("powershell", "-NoProfile", "-NonInteractive", "-Command", _BRIGHTNESS_PS, env=env,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except (asyncio.TimeoutError, OSError) as exc:
        return ToolResult.fail("set_brightness", f"Could not change brightness: {type(exc).__name__}", "failed")
    parts = out.decode(errors="replace").split()
    if proc.returncode != 0 or len(parts) != 2:
        return ToolResult.fail("set_brightness", "This display does not support software brightness control (typical for external monitors). "
                               "Use the monitor's buttons, or open the Display settings page.", "not_supported")
    before, after = int(parts[0]), int(parts[1])
    return ToolResult.ok("set_brightness", "display", f"Brightness {before}% -> {after}%", before=before, after=after)


class OpenAppArgs(BaseModel):
    name: str = Field(description="application name or path, e.g. 'notepad'")


@tool("open_application", "Launch a desktop app, or an OS settings page such as 'ms-settings:display' (never use open_url for those)", OpenAppArgs, "system")
async def open_application(ctx: ToolContext, a: OpenAppArgs) -> ToolResult:
    try:
        if sys.platform == "win32":
            os.startfile(a.name)  # type: ignore[attr-defined]  # noqa: S606
        else:
            subprocess.Popen([a.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    except OSError as exc:
        return ToolResult.fail("open_application", f"Cannot start {a.name}: {exc}", "not_found", target=a.name)
    await asyncio.sleep(1.5)
    return ToolResult.ok("open_application", a.name, f"Launched {a.name}")


class SendEmailArgs(BaseModel):
    to: str
    subject: str
    body: str


@tool("send_email", "Send an email through the configured SMTP account", SendEmailArgs, "system")
async def send_email(ctx: ToolContext, a: SendEmailArgs) -> ToolResult:
    s = ctx.settings
    if not (s.smtp_host and s.smtp_from):
        return ToolResult.fail("send_email", "SMTP is not configured (SMTP_HOST / SMTP_FROM)", "not_configured", target=a.to)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, a.to, a.subject
    msg.set_content(a.body)

    def _send() -> None:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    try:
        await asyncio.to_thread(_send)
    except (smtplib.SMTPException, OSError) as exc:
        return ToolResult.fail("send_email", f"Email failed: {type(exc).__name__}", "send_failed", target=a.to)
    return ToolResult.ok("send_email", a.to, f"Email sent to {a.to}")


class InstallArgs(BaseModel):
    manager: str = Field(description="winget | pip | npm")
    package: str


@tool("install_software", "Install a package with winget/pip/npm (needs approval)", InstallArgs, "system")
async def install_software(ctx: ToolContext, a: InstallArgs) -> ToolResult:
    cmds = {"winget": ["winget", "install", "--accept-source-agreements", "--accept-package-agreements", "-e", "--id"],
            "pip": [sys.executable, "-m", "pip", "install"], "npm": ["npm", "install", "-g"]}
    if a.manager not in cmds or not a.package.replace("-", "").replace(".", "").replace("_", "").isalnum():
        return ToolResult.fail("install_software", "Unsupported package manager or invalid package name", "invalid_args", target=a.package)
    try:
        proc = await asyncio.create_subprocess_exec(*cmds[a.manager], a.package, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except (FileNotFoundError, asyncio.TimeoutError) as exc:
        return ToolResult.fail("install_software", f"{type(exc).__name__}: {a.manager}", "install_failed", target=a.package)
    ok = proc.returncode == 0
    return ToolResult(success=ok, action="install_software", target=a.package, message="Installed" if ok else f"Exit code {proc.returncode}",
                      error_code=None if ok else "install_failed", data={"output": redact_text(out.decode(errors="replace"))[-1500:]})
