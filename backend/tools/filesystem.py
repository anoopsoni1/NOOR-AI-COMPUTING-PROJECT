"""File-system tools plus path resolution/policy helpers (shared with the permission engine)."""
from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ToolContext, ToolResult, tool

_FOLDER_NAMES = {"desktop": "Desktop", "downloads": "Downloads", "documents": "Documents", "pictures": "Pictures", "music": "Music", "videos": "Videos"}
_WIN_VALUE = {"desktop": "Desktop", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "documents": "Personal",
              "pictures": "My Pictures", "music": "My Music", "videos": "My Video"}


def known_folders() -> dict[str, Path]:
    """Real locations of the user's standard folders (handles OneDrive-redirected Desktop/Documents)."""
    home = Path.home()
    out = {"home": home}
    for key, name in _FOLDER_NAMES.items():
        path = home / name
        if sys.platform == "win32":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                    path = Path(os.path.expandvars(winreg.QueryValueEx(k, _WIN_VALUE[key])[0]))
            except OSError:
                pass
        out[key] = path
    return out


def resolve_path(raw: str) -> Path:
    """Expand ~, %VARS% and well-known folder aliases like 'Desktop/notes.txt'."""
    raw = os.path.expandvars(raw.strip().strip('"'))
    p = Path(raw).expanduser()
    if not p.is_absolute():
        folders = known_folders()
        first = p.parts[0].lower() if p.parts else ""
        p = folders[first].joinpath(*p.parts[1:]) if first in folders else folders["home"] / p
    return p.resolve()


_SENSITIVE_NAMES = (".env", "id_rsa", "id_ed25519", "id_ecdsa", "credentials", "passwords", "wallet.dat", "login data", "cookies", "secrets")
_SENSITIVE_SUFFIX = (".pem", ".key", ".pfx", ".p12", ".kdbx", ".ppk", ".keystore")
_SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".azure", ".docker"}
_EXECUTABLE_SUFFIX = (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".sh", ".scr", ".com", ".dll", ".reg")


def is_sensitive_path(p: Path) -> bool:
    parts = [x.lower() for x in p.parts]
    name = p.name.lower()
    return (any(d in parts for d in _SENSITIVE_DIRS) or name.startswith(_SENSITIVE_NAMES)
            or name.endswith(_SENSITIVE_SUFFIX) or "appdata" in parts and ("login data" in name or "cookies" in name))


def is_system_path(p: Path) -> bool:
    roots = [os.environ.get(v) for v in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")]
    s = str(p).lower()
    if any(r and s.startswith(r.lower()) for r in roots):
        return True
    return sys.platform != "win32" and p.parts[:2] in {("/", "etc"), ("/", "usr"), ("/", "bin"), ("/", "sbin"), ("/", "boot"), ("/", "sys")}


def is_executable_name(p: Path) -> bool:
    return p.name.lower().endswith(_EXECUTABLE_SUFFIX)


def inside_home(p: Path) -> bool:
    try:
        p.relative_to(Path.home().resolve())
        return True
    except ValueError:
        return False


# ---- tools ---------------------------------------------------------------------------------------------
class CreateFileArgs(BaseModel):
    path: str = Field(description="file path, e.g. 'Desktop/tasks.txt'")
    content: str = Field("", description="text content (use step.generate when it depends on gathered info)")
    overwrite: bool = True


@tool("create_file", "Create a text file (fails if it exists unless overwrite=true)", CreateFileArgs, "filesystem")
async def create_file(ctx: ToolContext, a: CreateFileArgs) -> ToolResult:
    p = resolve_path(a.path)
    if p.exists() and not a.overwrite:
        return ToolResult.fail("create_file", f"{p} already exists", "already_exists", target=str(p))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a.content, encoding="utf-8")
    return ToolResult.ok("create_file", str(p), f"Created {p.name} ({len(a.content)} chars)", path=str(p), bytes=p.stat().st_size)


class ReadFileArgs(BaseModel):
    path: str
    max_chars: int = Field(8000, ge=1, le=50000)


@tool("read_file", "Read a text file", ReadFileArgs, "filesystem")
async def read_file(ctx: ToolContext, a: ReadFileArgs) -> ToolResult:
    p = resolve_path(a.path)
    if not p.is_file():
        return ToolResult.fail("read_file", f"{p} is not a file", "not_found", target=str(p))
    text = p.read_text(encoding="utf-8", errors="replace")
    return ToolResult.ok("read_file", str(p), f"Read {len(text)} chars from {p.name}", content=text[: a.max_chars], truncated=len(text) > a.max_chars)


class MoveFileArgs(BaseModel):
    source: str
    destination: str = Field(description="new path, or an existing/new folder to move into")


def _move(src: Path, dst: Path) -> Path:
    if dst.is_dir() or str(dst).endswith(("/", "\\")):
        dst = dst / src.name
    if dst.exists():
        raise FileExistsError(f"{dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


@tool("move_file", "Move or rename one file/folder", MoveFileArgs, "filesystem")
async def move_file(ctx: ToolContext, a: MoveFileArgs) -> ToolResult:
    src, dst = resolve_path(a.source), resolve_path(a.destination)
    if not src.exists():
        return ToolResult.fail("move_file", f"{src} does not exist", "not_found", target=str(src))
    try:
        final = _move(src, dst)
    except OSError as exc:
        return ToolResult.fail("move_file", str(exc), "move_failed", target=str(src))
    return ToolResult.ok("move_file", str(src), f"Moved to {final}", destination=str(final))


class Move(BaseModel):
    source: str
    destination: str


class MoveFilesArgs(BaseModel):
    moves: list[Move] = Field(min_length=1, max_length=500, description="list of {source, destination}")


@tool("move_files", "Move many files in one approved batch (e.g. organise a folder)", MoveFilesArgs, "filesystem")
async def move_files(ctx: ToolContext, a: MoveFilesArgs) -> ToolResult:
    done, errors = [], []
    for mv in a.moves:
        src, dst = resolve_path(mv.source), resolve_path(mv.destination)
        try:
            if not src.exists():
                raise FileNotFoundError(f"{src} does not exist")
            done.append(str(_move(src, dst)))
        except OSError as exc:
            errors.append(f"{src.name}: {exc}")
    if errors and not done:
        return ToolResult.fail("move_files", f"No files moved: {errors[0]}", "move_failed", errors=errors)
    msg = f"Moved {len(done)} file(s)" + (f", {len(errors)} failed" if errors else "")
    return ToolResult(success=not errors, action="move_files", target=f"{len(a.moves)} files", message=msg,
                      error_code="partial_failure" if errors else None, data={"moved": done, "errors": errors})


class MkdirArgs(BaseModel):
    path: str


@tool("make_directory", "Create a folder (and parents)", MkdirArgs, "filesystem")
async def make_directory(ctx: ToolContext, a: MkdirArgs) -> ToolResult:
    p = resolve_path(a.path)
    p.mkdir(parents=True, exist_ok=True)
    return ToolResult.ok("make_directory", str(p), f"Folder ready: {p}", path=str(p))


class ListDirArgs(BaseModel):
    path: str
    pattern: str = Field("*", description="glob such as '*.pdf'")
    recursive: bool = False


@tool("list_directory", "List files in a folder, optionally filtered by glob", ListDirArgs, "filesystem")
async def list_directory(ctx: ToolContext, a: ListDirArgs) -> ToolResult:
    p = resolve_path(a.path)
    if not p.is_dir():
        return ToolResult.fail("list_directory", f"{p} is not a folder", "not_found", target=str(p))
    it = p.rglob("*") if a.recursive else p.iterdir()
    entries = []
    for e in it:
        if fnmatch.fnmatch(e.name.lower(), a.pattern.lower()) and not is_sensitive_path(e):
            try:
                entries.append({"path": str(e), "name": e.name, "type": "dir" if e.is_dir() else "file", "size": e.stat().st_size if e.is_file() else 0})
            except OSError:
                continue
        if len(entries) >= 500:
            break
    return ToolResult.ok("list_directory", str(p), f"{len(entries)} item(s) matching '{a.pattern}' in {p.name}", entries=entries, count=len(entries))
