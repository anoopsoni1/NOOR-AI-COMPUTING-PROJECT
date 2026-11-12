"""End-to-end tests of the LangGraph loop with a scripted LLM (no network, no browser, no desktop)."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import mongomock
import pytest

from backend import tools  # noqa: F401
from backend.agent.planner import PlanOut, RecoveryOut, StepIn
from backend.agent.state import AgentState, Deps, StepStatus
from backend.config import Settings
from backend.database.repository import Repository
from backend.security.permissions import PermissionEngine, Risk
from backend.services.hub import EventHub
from backend.services.memory import MemoryOut
from backend.services.prefs import effective
from backend.services.tasks import TaskManager
from backend.tools.base import REGISTRY
from backend.tools.computer import Computer
from backend.vision.screen_analyzer import JudgeOut

SANDBOX = Path.home() / "nova_pytest_sandbox"


class FakeLLM:
    configured = True

    def __init__(self, plans: list[Any]) -> None:
        self.plans = list(plans)

    async def structured(self, system: str, user: str, schema: type, images=None, retries: int = 2):  # noqa: ANN001
        if schema in (PlanOut, RecoveryOut):
            return self.plans.pop(0)
        if schema is JudgeOut:
            return JudgeOut(met=True)
        if schema is MemoryOut:
            return MemoryOut()
        raise AssertionError(schema)

    async def text(self, *a: Any, **k: Any) -> str:
        return "Generated text from observations."


class FakeBrowser:
    is_open = False
    last_url = ""
    headless = True

    async def close(self) -> None:
        self.closed = True


class FakeHub(EventHub):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    async def broadcast(self, event: dict) -> None:
        self.events.append(event)


@pytest.fixture(autouse=True)
def sandbox():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir()
    yield
    shutil.rmtree(SANDBOX, ignore_errors=True)


def make_manager(plans: list[Any]) -> tuple[TaskManager, FakeHub, PermissionEngine]:
    s = Settings(_env_file=None, llm_api_key="test", data_dir=SANDBOX / "data", approval_timeout_s=5)
    repo = Repository(mongomock.MongoClient()["t"])
    hub, perms = FakeHub(), PermissionEngine(s, repo)
    deps = Deps(settings=s, llm=FakeLLM(plans), registry=REGISTRY, permissions=perms, repo=repo, hub=hub, browser=FakeBrowser(),
                computer=Computer(False), analyzer=None, prefs=effective(s, {}))
    return TaskManager(deps), hub, perms


async def wait_done(m: TaskManager, timeout: float = 10) -> None:
    await asyncio.wait_for(m._job, timeout)  # type: ignore[arg-type]


def step(tool: str, args: dict, desc: str = "step", **kw: Any) -> StepIn:
    return StepIn(tool=tool, args=args, description=desc, **kw)


# ---- permissions ---------------------------------------------------------------------------------------------
def test_permission_classification():
    p = PermissionEngine(Settings(_env_file=None), None)
    assert p.decide("open_url", {"url": "https://example.com"}).risk is Risk.SAFE
    assert p.decide("click", {"target": "Download button"}).risk is Risk.SAFE
    assert p.decide("click", {"target": "Place order"}).risk is Risk.BLOCKED
    assert p.decide("type_text", {"text": "x", "target": "password field"}).risk is Risk.BLOCKED
    assert p.decide("click", {"x": 1, "y": 2, "mode": "desktop"}).risk is Risk.CONFIRM
    assert p.decide("move_files", {"moves": [{"source": "Downloads/a.pdf", "destination": "Downloads/PDF/a.pdf"}]}).risk is Risk.CONFIRM
    assert p.decide("run_shell", {"command": "rm -rf /"}).risk is Risk.BLOCKED
    assert p.decide("run_shell", {"command": "git status"}).risk is Risk.CONFIRM
    assert p.decide("read_file", {"path": "~/.ssh/id_rsa"}).risk is Risk.BLOCKED
    assert p.decide("create_file", {"path": "Desktop/x.txt", "content": "hi"}).risk is Risk.SAFE
    assert p.decide("send_email", {"to": "a@b.c", "subject": "s", "body": "b"}).risk is Risk.CONFIRM
    assert p.decide("made_up_tool", {}).risk is Risk.BLOCKED
    p.rules["send_email"] = "allow"
    assert p.decide("send_email", {"to": "a@b.c", "subject": "s", "body": "b"}).risk is Risk.SAFE
    p.rules["run_shell"] = "allow"  # user rules never lift BLOCKED
    assert p.decide("run_shell", {"command": "del file"}).risk is Risk.BLOCKED


def test_capabilities():
    from backend.security.permissions import CAPABILITIES, CAPABILITY_MODES, DEFAULT_CAPABILITY_MODE, capabilities_for
    assert "MICROPHONE" in CAPABILITIES
    assert "BROWSER" in CAPABILITIES
    assert "FILESYSTEM" in CAPABILITIES
    assert "ENABLED" in CAPABILITY_MODES and "DISABLED" in CAPABILITY_MODES and "ASK" in CAPABILITY_MODES
    assert capabilities_for("open_url") == ["BROWSER"]
    assert capabilities_for("create_file") == ["FILESYSTEM"]

    p = PermissionEngine(Settings(_env_file=None), None)
    assert p.capability_enabled("FILESYSTEM") is True
    # Disabling capability blocks the tool
    p.capabilities["FILESYSTEM"] = "DISABLED"
    assert p.capability_enabled("FILESYSTEM") is False
    assert p.decide("create_file", {"path": "Desktop/x.txt", "content": "hi"}).risk is Risk.BLOCKED

    # Ask mode turns safe into confirm
    p.capabilities["BROWSER"] = "ASK"
    assert p.decide("open_url", {"url": "https://example.com"}).risk is Risk.CONFIRM


# ---- demo 3: create a file ---------------------------------------------------------------------------------------
async def test_create_file_with_generated_content():
    target = "nova_pytest_sandbox/tasks.txt"
    plan = PlanOut(understanding="write a file", steps=[
        step("create_file", {"path": target}, "Write task list", generate={"arg": "content", "instruction": "task list"},
             verify={"kind": "file_exists", "value": target}),
    ])
    m, hub, _ = make_manager([plan])
    await m.start("Create a text file containing today's task list")
    await wait_done(m)
    t = m.run.task
    assert t.status == AgentState.COMPLETED, t.log[-3:]
    assert (SANDBOX / "tasks.txt").read_text() == "Generated text from observations."
    assert t.plan[0].status == StepStatus.DONE
    assert any(e["type"] == "finished" for e in hub.events)
    assert t.public()["actions"][0]["args"]["content"].startswith("<")


# ---- demo 6: failure -> observation -> re-plan -> recovery -> success -------------------------------------------------
async def test_failure_replan_recovery():
    bad = PlanOut(understanding="read notes", steps=[step("read_file", {"path": "nova_pytest_sandbox/missing.txt"}, "Read notes")])
    fix = RecoveryOut(decision="replan", reason="file missing, create it instead",
                      steps=[step("create_file", {"path": "nova_pytest_sandbox/missing.txt", "content": "now exists"}, "Create notes")])
    m, _, _ = make_manager([bad, fix])
    await m.start("Read my notes file")
    await wait_done(m)
    t = m.run.task
    assert t.status == AgentState.COMPLETED, t.log[-4:]
    assert t.replans == 1
    assert [s.status for s in t.plan] == [StepStatus.SKIPPED, StepStatus.DONE]
    assert (SANDBOX / "missing.txt").exists()


async def test_replan_budget_prevents_infinite_loop():
    bad = lambda: PlanOut(understanding="x", steps=[step("read_file", {"path": "nova_pytest_sandbox/nope.txt"})])  # noqa: E731
    again = lambda: RecoveryOut(decision="replan", reason="try again", steps=[step("read_file", {"path": "nova_pytest_sandbox/nope2.txt"})])  # noqa: E731
    m, _, _ = make_manager([bad(), again(), again(), again(), again()])
    await m.start("Read a file that never exists")
    await wait_done(m)
    assert m.run.task.status == AgentState.FAILED
    assert m.run.task.replans <= 3


# ---- permissions in the loop -----------------------------------------------------------------------------------------
async def test_confirmation_required_and_approved():
    (SANDBOX / "a.pdf").write_text("x")
    plan = PlanOut(understanding="organise", steps=[step("move_files", {"moves": [{"source": "nova_pytest_sandbox/a.pdf", "destination": "nova_pytest_sandbox/PDFs/a.pdf"}]})])
    m, hub, perms = make_manager([plan])
    await m.start("Organise PDFs")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if m.run.task.pending_permission:
            break
    req = m.run.task.pending_permission
    assert req and m.run.task.status == AgentState.WAITING_FOR_PERMISSION
    assert req.title.startswith("Move 1 file")
    assert perms.respond(req.id, True)
    await wait_done(m)
    assert m.run.task.status == AgentState.COMPLETED
    assert (SANDBOX / "PDFs" / "a.pdf").exists() and not (SANDBOX / "a.pdf").exists()


async def test_denied_action_is_not_executed():
    (SANDBOX / "a.pdf").write_text("x")
    plan = PlanOut(understanding="move", steps=[step("move_file", {"source": "nova_pytest_sandbox/a.pdf", "destination": "nova_pytest_sandbox/b.pdf"})])
    abort = RecoveryOut(decision="abort", reason="user denied the move")
    m, _, perms = make_manager([plan, abort])
    await m.start("Rename a.pdf")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if m.run.task.pending_permission:
            break
    perms.respond(m.run.task.pending_permission.id, False)
    await wait_done(m)
    assert m.run.task.status == AgentState.FAILED
    assert (SANDBOX / "a.pdf").exists() and not (SANDBOX / "b.pdf").exists()


async def test_blocked_action_never_runs():
    plan = PlanOut(understanding="x", steps=[step("run_shell", {"command": "rm -rf ."})])
    m, _, _ = make_manager([plan, RecoveryOut(decision="abort", reason="blocked")])
    await m.start("Delete everything")
    await wait_done(m)
    assert m.run.task.status == AgentState.FAILED
    assert not m.run.task.actions  # nothing was executed


# ---- ask user / stop ------------------------------------------------------------------------------------------------------
async def test_ask_user_then_continue():
    plan = PlanOut(understanding="x", steps=[step("ask_user", {"question": "What is your GitHub username?"}),
                                              step("create_file", {"path": "nova_pytest_sandbox/u.txt", "content": "ok"})])
    m, _, _ = make_manager([plan])
    await m.start("Open my repo")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if m.run.task.status == AgentState.WAITING_FOR_USER:
            break
    assert m.run.task.pending_question
    assert m.respond("octocat")
    await wait_done(m)
    assert m.run.task.status == AgentState.COMPLETED
    assert any("octocat" in n for n in m.run.notes)


async def test_emergency_stop_while_waiting():
    plan = PlanOut(understanding="x", steps=[step("ask_user", {"question": "Continue?"})])
    m, _, _ = make_manager([plan])
    await m.start("Wait for me")
    for _ in range(100):
        await asyncio.sleep(0.05)
        if m.run.task.status == AgentState.WAITING_FOR_USER:
            break
    assert await m.stop()
    assert m.run.task.status == AgentState.CANCELLED
    assert not m.active


async def test_invalid_plan_is_rejected_and_retried():
    bad = PlanOut(understanding="x", steps=[step("exec_python", {"code": "import os"})])
    good = PlanOut(understanding="x", steps=[step("make_directory", {"path": "nova_pytest_sandbox/d"})])
    m, _, _ = make_manager([bad, good])
    await m.start("Make a folder")
    await wait_done(m)
    assert m.run.task.status == AgentState.COMPLETED
    assert (SANDBOX / "d").is_dir()
