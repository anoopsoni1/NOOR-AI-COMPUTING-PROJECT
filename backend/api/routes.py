"""REST + WebSocket API."""
from __future__ import annotations
import asyncio
import json
import logging
import subprocess
import psutil
import subprocess
import psutil
from typing import Any, Literal, Optional
from fastapi import Response, APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from backend.agent.state import TERMINAL_STATES
from backend.database import connection
from backend.database.models import MemoryDoc
from backend.database.repository import run_db
from backend.llm.providers import LLMError, ProviderModel
from backend.security.permissions import CAPABILITIES, CAPABILITY_MODES, DEFAULT_CAPABILITY_MODE, capabilities_for
from backend.services.prefs import Preferences, defaults, effective
from backend.services.tasks import TaskBusy
from backend.tools import REGISTRY
log = logging.getLogger("noor.api")
router = APIRouter(prefix="/api")

def ctx(request: Request) -> Any:
    return request.app.state.noor

# ---- models ---------------------------------------------------------------------------------------
class StartTask(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)

class Respond(BaseModel):
    text: str = Field(max_length=2000)

class Approval(BaseModel):
    approve: bool

class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=300)
    category: Optional[Literal["preference", "app", "folder", "workflow", "outcome", "other"]] = None

class RuleIn(BaseModel):
    policy: Literal["default", "ask", "allow"]

# ---- health / meta ---------------------------------------------------------------------------------
@router.get("/health")
async def health(n: Any = Depends(ctx)) -> dict[str, Any]:
    s = n.deps.settings
    llm = n.deps.llm
    return {"status": "ok", "llmConfigured": llm.configured, "provider": llm.provider or s.llm_provider, "model": llm.model or s.llm_model,
            "visionEnabled": s.vision_enabled, "dbInMemory": connection.in_memory, "busy": n.tasks.active,
            "desktopAutomation": s.desktop_automation_enabled, "voice": n.audio.status()}

@router.get("/stats")
async def stats(n: Any = Depends(ctx)) -> dict[str, Any]:
    return {**await run_db(n.deps.repo.stats), "today": await run_db(n.deps.repo.today_stats), "recent": await run_db(n.deps.repo.list_tasks, 5)}


def _get_system_metrics() -> dict[str, Any]:
    cpu_pct = psutil.cpu_percent(interval=None)
    vmem = psutil.virtual_memory()
    ram_used_mb = int(vmem.used / (1024 * 1024))
    ram_total_mb = int(vmem.total / (1024 * 1024))
    ram_pct = round(vmem.percent, 1)

    gpu_total_mb, gpu_used_mb, gpu_util = 0, 0, 0
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, timeout=1.0
        ).strip()
        if out:
            parts = [p.strip() for p in out.split(",")]
            if len(parts) >= 3:
                gpu_total_mb = int(float(parts[0]))
                gpu_used_mb = int(float(parts[1]))
                gpu_util = int(float(parts[2]))
    except Exception:
        pass

    return {
        "cpu_percent": cpu_pct,
        "ram_used_mb": ram_used_mb,
        "ram_total_mb": ram_total_mb,
        "ram_percent": ram_pct,
        "gpu_total_mb": gpu_total_mb,
        "gpu_used_mb": gpu_used_mb,
        "gpu_util_percent": gpu_util,
    }


@router.get("/system/metrics")
async def system_metrics(n: Any = Depends(ctx)) -> dict[str, Any]:
    sys_stats = await asyncio.to_thread(_get_system_metrics)
    llm = n.deps.llm
    s = n.deps.settings

    task_info: dict[str, Any] = {}
    if n.tasks.active and n.tasks.run:
        task = n.tasks.run.task
        task_info = {
            "taskId": task.id,
            "goal": task.goal,
            "status": task.status.value,
            "executions": n.tasks.run.executions,
            "actions": [
                {
                    "tool": a.tool,
                    "duration_ms": a.duration_ms,
                    "success": a.success,
                }
                for a in task.actions[-5:]
            ],
        }

    recent_latency = n.audio.metrics.history[-1] if (hasattr(n.audio, "metrics") and n.audio.metrics and n.audio.metrics.history) else None

    return {
        "system": sys_stats,
        "llm": {
            "provider": getattr(llm, "provider_name", s.llm_provider),
            "model": getattr(llm, "model", s.llm_model),
            "configured": llm.configured,
        },
        "voice": {
            "tts_provider": getattr(s, "tts_provider", "kokoro"),
            "tts_voice": getattr(s, "tts_voice", "af_heart"),
            "language": getattr(s, "response_language", "english"),
            "stt_device": getattr(getattr(n.audio.stt, "local", None), "device", "cpu"),
        },
        "task": task_info,
        "latency": recent_latency,
    }

# ---- tasks ---------------------------------------------------------------------------------------------
@router.post("/tasks", status_code=202)
async def start_task(body: StartTask, n: Any = Depends(ctx)) -> dict[str, str]:
    try:
        return {"id": await n.tasks.start(body.goal)}
    except TaskBusy as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

@router.get("/tasks")
async def list_tasks(limit: int = 50, skip: int = 0, status: Optional[str] = None, n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    return await run_db(n.deps.repo.list_tasks, min(limit, 200), skip, status)

@router.get("/tasks/current")
async def current_task(n: Any = Depends(ctx)) -> Optional[dict[str, Any]]:
    return n.tasks.snapshot()

@router.get("/tasks/{task_id}")
async def get_task(task_id: str, n: Any = Depends(ctx)) -> dict[str, Any]:
    live = n.tasks.run
    if live and live.task.id == task_id:
        task = live.task.public()
    else:
        task = await run_db(n.deps.repo.get_task, task_id)
        if not task:
            raise HTTPException(404, "Task not found")
    task["executions"] = await run_db(n.deps.repo.list_executions, task_id)
    return task

@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: str, n: Any = Depends(ctx)) -> None:
    if n.tasks.run and n.tasks.run.task.id == task_id and n.tasks.active:
        raise HTTPException(409, "Task is running")
    if not await run_db(n.deps.repo.delete_task, task_id):
        raise HTTPException(404, "Task not found")

@router.post("/tasks/stop")
async def stop_task(n: Any = Depends(ctx)) -> dict[str, bool]:
    await n.audio.emergency_stop()
    return {"stopped": True}

@router.post("/tasks/pause")
async def pause_task(n: Any = Depends(ctx)) -> dict[str, bool]:
    return {"ok": await n.tasks.pause()}

@router.post("/tasks/resume")
async def resume_task(n: Any = Depends(ctx)) -> dict[str, bool]:
    return {"ok": await n.tasks.resume()}

@router.post("/tasks/respond")
async def respond_task(body: Respond, n: Any = Depends(ctx)) -> dict[str, bool]:
    if not n.tasks.respond(body.text):
        raise HTTPException(409, "NOOR is not waiting for a response")
    return {"ok": True}

# ---- permissions ----------------------------------------------------------------------------------------
@router.post("/permissions/requests/{request_id}")
async def answer_permission(request_id: str, body: Approval, n: Any = Depends(ctx)) -> dict[str, bool]:
    if not n.deps.permissions.respond(request_id, body.approve):
        raise HTTPException(404, "No such pending request")
    return {"ok": True}

@router.get("/permissions/rules")
async def get_rules(n: Any = Depends(ctx)) -> list[dict[str, str]]:
    rules = await run_db(n.deps.repo.list_rules)
    return [{"tool": t.name, "category": t.category, "description": t.description, "policy": rules.get(t.name, "default")}
            for t in (REGISTRY.get(x) for x in REGISTRY.names()) if t]

@router.put("/permissions/rules/{tool}")
async def set_rule(tool: str, body: RuleIn, n: Any = Depends(ctx)) -> dict[str, str]:
    if REGISTRY.get(tool) is None:
        raise HTTPException(404, "Unknown tool")
    await run_db(n.deps.repo.set_rule, tool, body.policy)
    await n.deps.permissions.load_rules()
    return {"tool": tool, "policy": body.policy}

@router.get("/permissions/capabilities")
async def get_capabilities(n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    """Exactly what NOOR may access, which tools each grant unlocks, and the current mode."""
    tools_by_cap: dict[str, set[str]] = {c: set() for c in CAPABILITIES}
    for name in REGISTRY.names():
        for variant in ({}, {"mode": "desktop"}):
            for cap in capabilities_for(name, variant):
                tools_by_cap[cap].add(name)
    perms = n.deps.permissions
    return [{"capability": c, "label": label, "description": desc, "mode": perms.capability_mode(c), "default": DEFAULT_CAPABILITY_MODE[c],
             "tools": sorted(tools_by_cap[c])} for c, (label, desc) in CAPABILITIES.items()]

class CapabilityIn(BaseModel):
    mode: Literal["ENABLED", "ASK", "DISABLED"]

@router.put("/permissions/capabilities/{capability}")
async def set_capability(capability: str, body: CapabilityIn, n: Any = Depends(ctx)) -> dict[str, str]:
    if capability not in CAPABILITIES or body.mode not in CAPABILITY_MODES:
        raise HTTPException(404, "Unknown capability")
    await run_db(n.deps.repo.set_capability, capability, body.mode)
    await n.deps.permissions.load_rules()
    if capability == "MICROPHONE":
        n.audio.set_mic_enabled(n.audio.mic_enabled)  # re-evaluate immediately: a revoked microphone must stop listening now
        await n.hub.broadcast({"type": "voice.state", **n.audio.status()})
    await n.hub.broadcast({"type": "permission.capability", "capability": capability, "mode": body.mode})
    return {"capability": capability, "mode": body.mode}

@router.get("/permissions/log")
async def audit_log(limit: int = 100, n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    return await run_db(n.deps.repo.list_audit, min(limit, 500))

# ---- memories --------------------------------------------------------------------------------------------
@router.get("/memories")
async def list_memories(n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    return await run_db(n.deps.repo.list_memories)

@router.post("/memories", status_code=201)
async def add_memory(body: MemoryIn, n: Any = Depends(ctx)) -> dict[str, Any]:
    from backend.security.redaction import looks_sensitive
    if looks_sensitive(body.content):
        raise HTTPException(422, "That looks like sensitive data (password, key, card...). NOOR does not store those.")
    return await run_db(n.deps.repo.add_memory, MemoryDoc(content=body.content.strip(), category=body.category or "other", source="user"))

@router.put("/memories/{memory_id}")
async def edit_memory(memory_id: str, body: MemoryIn, n: Any = Depends(ctx)) -> dict[str, Any]:
    from backend.security.redaction import looks_sensitive
    if looks_sensitive(body.content):
        raise HTTPException(422, "That looks like sensitive data. NOOR does not store those.")
    doc = await run_db(n.deps.repo.update_memory, memory_id, body.content.strip(), body.category)
    if not doc:
        raise HTTPException(404, "Memory not found")
    return doc

@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, n: Any = Depends(ctx)) -> None:
    if not await run_db(n.deps.repo.delete_memory, memory_id):
        raise HTTPException(404, "Memory not found")

# ---- settings -----------------------------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(n: Any = Depends(ctx)) -> dict[str, Any]:
    stored = await run_db(n.deps.repo.get_preferences)
    s = n.deps.settings
    return {"preferences": effective(s, stored), "defaults": defaults(s),
            "environment": {"llmProvider": s.llm_provider, "llmModel": s.llm_model, "llmConfigured": s.llm_configured,
                            "visionModel": s.llm_vision_model or s.llm_model, "database": s.database_name,
                            "dbInMemory": connection.in_memory, "browserChannel": s.browser_channel or "chromium"}}

@router.put("/settings")
async def put_settings(body: Preferences, n: Any = Depends(ctx)) -> dict[str, Any]:
    await run_db(n.deps.repo.set_preferences, body.model_dump())
    n.deps.prefs.update(body.model_dump())
    n.deps.llm.apply_prefs(body.model_dump())  # next generation uses the new prefs
    try:
        await n.deps.llm.reconfigure(body.llm_provider or n.deps.settings.llm_provider, body.llm_model or None)
    except LLMError as exc:  # persisted; generation will surface a clear error instead
        log.warning("saved LLM settings but provider validation failed: %s", exc)
    n.audio.apply_prefs(body.model_dump())
    await n.deps.permissions.load_rules()  # risk policies live in the same preferences
    return await get_settings(n)

# ---- LLM provider / model switching ------------------------------------------------------------------
class LlmTestIn(BaseModel):
    provider: str
    model: Optional[str] = None

class LlmReconfigureIn(BaseModel):
    provider: str
    model: Optional[str] = None

@router.get("/llm/providers")
async def llm_providers(n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    """Every supported provider (Ollama, Groq, Gemini) with its configured state. No keys are returned."""
    return n.deps.llm.all_providers()

@router.get("/llm/models")
async def llm_models(provider: str, n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    """Available models for a provider. Ollama lists locally installed models; Groq/Gemini call their APIs."""
    try:
        models = await n.deps.llm.list_models_for(provider)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from None
    return [{"id": m.id, "label": m.label or m.id} for m in models]

@router.post("/llm/test")
async def llm_test(body: LlmTestIn, n: Any = Depends(ctx)) -> dict[str, Any]:
    """Test connectivity to a provider/model without persisting the choice."""
    try:
        await n.deps.llm.test_provider(body.provider, body.model)
    except LLMError as exc:
        return JSONResponse(
            {"ok": False, "provider": body.provider, "model": body.model or "", "message": str(exc)},
            status_code=400,
        )
    return {"ok": True, "provider": body.provider, "model": body.model or "", "message": "Connection OK"}

@router.put("/llm/reconfigure")
async def llm_reconfigure(body: LlmReconfigureIn, n: Any = Depends(ctx)) -> dict[str, Any]:
    """Switch provider/model live without restarting NOOR."""
    try:
        await n.deps.llm.reconfigure(body.provider, body.model)
    except LLMError as exc:
        raise HTTPException(400, str(exc)) from None
    try:
        prefs = await run_db(n.deps.repo.get_preferences)
        prefs["llm_provider"] = n.deps.llm.provider
        prefs["llm_model"] = n.deps.llm.model
        await run_db(n.deps.repo.set_preferences, prefs)
        n.deps.llm.apply_prefs(prefs)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not persist LLM preference: %s", exc)
    await n.hub.broadcast({"type": "voice.state", **n.audio.status()})
    return {"ok": True, "provider": n.deps.llm.provider, "model": n.deps.llm.model}

# ---- WebSocket -----------------------------------------------------------------------------------------------------
@router.websocket("/ws")  # mounted at /api/ws; also at /ws in main.py
async def ws_endpoint(ws: WebSocket) -> None:
    await handle_ws(ws)

async def handle_ws(ws: WebSocket) -> None:
    n = ws.app.state.noor
    token = n.deps.settings.api_token.get_secret_value()
    origin = ws.headers.get("origin")
    if (token and ws.query_params.get("token") != token) or (origin and origin not in n.deps.settings.allowed_origins):
        await ws.close(code=4403)
        return
    await ws.accept()
    await n.hub.connect(ws)
    if n.audio.mic_allowed:
        n.audio.set_mic_enabled(True)
    await ws.send_json({"type": "hello", "task": n.tasks.snapshot(), "voice": n.audio.status()})
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:  # microphone frame: 16 kHz mono PCM16
                await n.audio.feed_audio(msg["bytes"])
                continue
            try:
                data = json.loads(msg.get("text") or "{}")
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "stop":
                await n.audio.emergency_stop()
            elif kind == "permission_response":
                n.deps.permissions.respond(str(data.get("id", "")), bool(data.get("approve")))
            elif kind == "respond":
                n.tasks.respond(str(data.get("text", ""))[:2000])
            elif kind == "user_text":  # typed command: same pipeline as speech (never blocks the audio loop)
                asyncio.create_task(n.audio.submit_text(str(data.get("text", ""))[:2000], "text"))
            elif kind == "voice_enable":
                n.audio.set_mic_enabled(bool(data.get("enabled")))
            elif kind == "tts_enable":
                n.audio.set_tts_enabled(bool(data.get("enabled")))
            elif kind == "tts_idle":
                n.audio.speaker.client_idle(int(data.get("epoch", -1)))
            elif kind == "activate":
                await n.audio.activate()
            elif kind == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("websocket error")
    finally:
        n.hub.disconnect(ws)
        if not n.hub._clients:  # no UI left: stop listening and talking
            n.audio.set_mic_enabled(False)

# ---- Voice / TTS Endpoints -------------------------------------------------------------------------
@router.get("/voice")
async def voice_status(n: Any = Depends(ctx)) -> dict[str, Any]:
    return n.audio.status()

@router.get("/voice/status")
async def voice_tts_status(n: Any = Depends(ctx)) -> dict[str, Any]:
    """Status of the active TTS engine, model cached state, device, and sample rate."""
    from backend.voice.tts_manager import TTSManager
    return {
        **TTSManager.get_instance(n.deps.settings).get_status(),
        "audio_manager": n.audio.status(),
    }

@router.get("/voice/providers")
async def voice_providers(n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    """List all available TTS engines (Kokoro local, Piper, Edge, ElevenLabs, System SAPI)."""
    from backend.voice.tts_manager import TTSManager
    return TTSManager.get_instance(n.deps.settings).list_providers()

@router.get("/voice/voices")
async def voice_voices(provider: Optional[str] = None, language: Optional[str] = None, n: Any = Depends(ctx)) -> list[dict[str, Any]]:
    """List voices supported by the specified provider or filtered by language."""
    from backend.voice.tts_manager import TTSManager
    return TTSManager.get_instance(n.deps.settings).list_voices(provider=provider, lang=language)

@router.post("/voice/test")
async def voice_test(body: dict[str, Any], n: Any = Depends(ctx)) -> dict[str, Any]:
    """Test synthesis with performance metrics (latency, RTF, sample rate) and base64 audio."""
    from backend.voice.tts_manager import TTSManager
    mgr = TTSManager.get_instance(n.deps.settings)
    try:
        return await mgr.test_voice(
            provider=body.get("provider"),
            voice=body.get("voice"),
            text=body.get("text"),
            speed=float(body.get("speed", 1.0)),
            lang=body.get("language"),
        )
    except Exception as exc:
        raise HTTPException(500, f"Voice test failed: {exc}") from exc

@router.post("/voice/tts")
async def voice_tts(body: dict[str, Any], n: Any = Depends(ctx)) -> Response:
    """Synthesize arbitrary text into WAV audio bytes."""
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "Text must not be empty.")
    if len(text) > 5000:
        raise HTTPException(400, "Text length exceeds 5000 character limit.")
    from backend.voice.tts_manager import TTSManager
    mgr = TTSManager.get_instance(n.deps.settings)
    try:
        audio_bytes, mime = await mgr.synth(
            text=text,
            provider=body.get("provider"),
            voice=body.get("voice"),
            speed=float(body.get("speed", 1.0)),
            lang=body.get("language"),
        )
        return Response(
            content=audio_bytes,
            media_type=mime,
            headers={
                "Content-Disposition": "inline; filename=speech.wav",
                "X-Voice": body.get("voice") or mgr.active_voice,
                "X-Provider": body.get("provider") or mgr.active_provider,
            },
        )
    except Exception as exc:
        raise HTTPException(500, f"Synthesis failed: {exc}") from exc
