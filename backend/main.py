"""NOOR backend entrypoint:  python -m backend.main   (run from the `noor/` folder)"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import tools  # noqa: F401  (registers tools)
from backend.agent.state import Deps
from backend.api.routes import handle_ws, router
from backend.config import get_settings
from backend.conversation.manager import ConversationManager
from backend.database import connection
from backend.database.repository import Repository, run_db
from backend.llm import LLMClient
from backend.logging_setup import setup_logging
from backend.security.permissions import PermissionEngine
from backend.services.hub import EventHub
from backend.services.prefs import effective
from backend.services.retention import run_retention
from backend.services.tasks import TaskManager
from backend.tools.base import REGISTRY
from backend.tools.browser import BrowserManager
from backend.tools.computer import Computer
from backend.vision.screen_analyzer import ScreenAnalyzer
from backend.voice.audio_manager import AudioManager, Speaker
from backend.voice.speech_to_text import build_stt
from backend.voice.text_to_speech import build_tts_chain

log = logging.getLogger("noor")


@dataclass
class Noor:
    deps: Deps
    hub: EventHub
    tasks: TaskManager
    audio: AudioManager
    conversation: ConversationManager


async def _retention_loop(n: Noor) -> None:
    while True:
        try:
            stored = await run_db(n.deps.repo.get_preferences)
            await run_retention(n.deps.settings.screenshots_dir, n.deps.repo, effective(n.deps.settings, stored))
        except Exception:  # noqa: BLE001
            log.exception("retention run failed")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    setup_logging(s.log_level, s.data_dir / "logs")
    repo = Repository(await run_db(connection.connect, s))
    await run_db(repo.ensure_user)
    stored_prefs = await run_db(repo.get_preferences)
    prefs0 = effective(s, stored_prefs)
    llm = LLMClient(s, prefs0)
    hub = EventHub()
    perms = PermissionEngine(s, repo)
    await perms.load_rules()
    deps = Deps(settings=s, llm=llm, registry=REGISTRY, permissions=perms, repo=repo, hub=hub, browser=BrowserManager(s),
                computer=Computer(s.desktop_automation_enabled), analyzer=ScreenAnalyzer(llm),
                prefs=prefs0)
    tasks = TaskManager(deps)
    speaker = Speaker(hub, build_tts_chain(s), lambda: None)
    audio = AudioManager(s, hub, build_stt(s), speaker, tasks, deps.prefs, permissions=perms)
    session_id = await run_db(repo.create_session)
    conversation = ConversationManager(tasks=tasks, llm=llm, repo=repo, permissions=perms, browser=deps.browser, speaker=speaker, session_id=session_id, prefs=deps.prefs)
    audio.attach(conversation)
    deps.speaker = conversation  # the agent talks through the conversation layer (history + speech)
    noor = Noor(deps, hub, tasks, audio, conversation)
    app.state.noor = noor
    await audio.start()
    retention = asyncio.create_task(_retention_loop(noor))
    if not s.llm_configured:
        log.warning("LLM_API_KEY is not set — NOOR UI will start but tasks cannot run until you configure .env")
    log.info("NOOR backend ready on %s", s.backend_url)
    try:
        yield
    finally:
        retention.cancel()
        await noor.tasks.stop()
        await deps.browser.close()
        await llm.aclose()
        connection.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="NOOR — AI Computer Operator", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=s.allowed_origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Any:
        """The API can drive the user's computer, so reject cross-site callers and (if configured) missing tokens."""
        if request.method != "OPTIONS":
            origin = request.headers.get("origin")
            if origin and origin not in s.allowed_origins:
                return JSONResponse({"detail": "origin not allowed"}, status_code=403)
            token = s.api_token.get_secret_value()
            if token and request.url.path != "/api/health" and request.headers.get("x-noor-token") != token:
                return JSONResponse({"detail": "invalid token"}, status_code=401)
        return await call_next(request)

    app.include_router(router)

    @app.websocket("/ws")
    async def ws_root(ws: WebSocket) -> None:
        await handle_ws(ws)

    return app


app = create_app()


def main() -> None:
    s = get_settings()
    # No reload: Playwright needs the Windows Proactor event loop, which uvicorn's reloader replaces.
    uvicorn.run(app, host=s.host, port=s.port, log_config=None)


if __name__ == "__main__":
    main()

