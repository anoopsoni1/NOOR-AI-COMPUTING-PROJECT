"""LLM Client abstraction for Noor.

NOOR now supports Ollama (local), Groq and Google Gemini. The actual provider
logic lives in ``backend/llm/providers.py``; this client keeps the stable public
API used by the conversation manager, planner and executor:

    llm.text(system, user, images, max_tokens) -> str
    llm.structured(system, user, schema, images, retries) -> BaseModel
    llm.stream_text(system, user, max_tokens) -> AsyncIterator[str]

Provider and model can be swapped at runtime with ``reconfigure(...)`` without
restarting the application.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncIterator, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.llm.providers import (
    LLMError, LLMFormatError, LLMProvider, ProviderModel, build_provider, PROVIDERS, _normalize_http_error,
)

log = logging.getLogger("noor.llm")
T = TypeVar("T", bound=BaseModel)


def compact_schema(schema: dict[str, Any]) -> str:
    """JSON Schema without the noise (titles, defaults) to keep prompts small."""
    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in ("title", "default", "additionalProperties")}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return json.dumps(strip(schema), separators=(",", ":"))


def extract_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(text[start : end + 1])


class LLMUnreachable(LLMError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, prefs: dict[str, Any] | None = None) -> None:
        self.s = settings
        self.prefs = prefs or {}
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0)
        self._http = httpx.AsyncClient(timeout=settings.llm_timeout_s, limits=limits)
        self._provider: LLMProvider | None = None
        self._provider_name: str | None = None
        self._model: str | None = None
        name = (self.prefs.get("llm_provider") or settings.llm_provider).lower()
        prov_cls = PROVIDERS.get(name)
        default_m = prov_cls.default_model if prov_cls else "default"
        pref_m = self.prefs.get("llm_model")
        if pref_m and prov_cls:
            if name == "gemini" and not pref_m.startswith("gemini"):
                model = default_m
            elif name == "groq" and ("gemini" in pref_m or pref_m == "llama3.2"):
                model = default_m
            elif name == "ollama" and ("gemini" in pref_m or "qwen/" in pref_m):
                model = default_m
            else:
                model = pref_m
        elif settings.llm_provider.lower() == name and settings.llm_model:
            model = settings.llm_model
            if name == "gemini" and not model.startswith("gemini"):
                model = default_m
        else:
            model = default_m
        self._apply(name, model, validate=False)

    def apply_prefs(self, prefs: dict[str, Any] | None) -> None:
        """Update live prefs (called from /api/settings). The next generation uses them."""
        self.prefs = prefs or {}

    # ---- runtime reconfiguration ------------------------------------------------
    def _apply(self, provider_name: str, model: str, *, validate: bool) -> None:
        name = provider_name.lower()
        if name not in PROVIDERS:
            raise LLMError(f"Unsupported LLM provider '{name}'. Use one of {sorted(PROVIDERS)}")
        self._provider_name = name
        self._model = model
        try:
            self._provider = build_provider(self.s, name, model)
        except Exception as exc:  # noqa: BLE001 - provider build must not kill startup
            log.warning("could not build provider %s: %s", name, exc)
            self._provider = None

    async def reconfigure(self, provider_name: str | None = None, model: str | None = None) -> None:
        """Switch provider/model live. Raises LLMError if the provider cannot validate."""
        name = (provider_name or self._provider_name or self.s.llm_provider).lower()
        prov_cls = PROVIDERS.get(name)
        default_mod = prov_cls.default_model if prov_cls else "default"
        
        if name != self._provider_name:
            # When switching providers, validate whether requested model belongs to the new provider
            if model:
                if name == "gemini" and not model.startswith("gemini"):
                    model_id = default_mod
                elif name == "groq" and "gemini" in model:
                    model_id = default_mod
                elif name == "ollama" and ("gemini" in model or "qwen/" in model):
                    model_id = default_mod
                else:
                    model_id = model
            else:
                model_id = default_mod
        else:
            model_id = model or self._model or default_mod
        self._apply(name, model_id, validate=False)
        await self.validate()

    async def validate(self) -> None:
        """Test connectivity to the current provider (used by the /api/llm/test endpoint)."""
        if self._provider is None:
            raise LLMError(f"Provider '{self._provider_name}' is not available.")
        await self._provider.validate(self._http)

    # ---- provider introspection -------------------------------------------------
    def provider_info(self, name: str | None = None) -> dict[str, Any]:
        """Static info about a provider (no network): id, display name, API-key requirement, configured state."""
        name = (name or self._provider_name or self.s.llm_provider).lower()
        if name not in PROVIDERS:
            raise LLMError(f"Unsupported LLM provider '{name}'. Use one of {sorted(PROVIDERS)}")
        cls = PROVIDERS[name]
        provider = build_provider(self.s, name, cls.default_model)
        return {
            "id": name,
            "name": cls.display_name,
            "needs_api_key": cls.needs_api_key,
            "configured": provider.configured,
            "default_model": cls.default_model,
        }

    def all_providers(self) -> list[dict[str, Any]]:
        """Info for every supported provider (Ollama, Groq, Gemini)."""
        return [self.provider_info(name) for name in PROVIDERS]

    async def list_models_for(self, name: str) -> list[ProviderModel]:
        """List available models for a provider (hits the provider API; may require its key)."""
        name = name.lower()
        if name not in PROVIDERS:
            raise LLMError(f"Unsupported LLM provider '{name}'. Use one of {sorted(PROVIDERS)}")
        provider = build_provider(self.s, name, PROVIDERS[name].default_model)
        return await provider.list_models(self._http)

    async def test_provider(self, name: str, model: str | None = None) -> None:
        """Test connectivity to a provider/model without persisting the choice."""
        name = name.lower()
        if name not in PROVIDERS:
            raise LLMError(f"Unsupported LLM provider '{name}'. Use one of {sorted(PROVIDERS)}")
        model_id = model or PROVIDERS[name].default_model
        provider = build_provider(self.s, name, model_id)
        await provider.validate(self._http)

    # ---- fallback provider (explicit opt-in only; never silently changes models) --
    def _fallback(self) -> tuple[str, str] | None:
        """Resolve the configured fallback provider/model, or None when fallback is off or invalid."""
        if not self.prefs.get("llm_fallback_enabled"):
            return None
        fb = (self.prefs.get("llm_fallback_provider") or "none").lower()
        if fb == "none" or fb not in PROVIDERS or fb == self._provider_name:
            return None
        fb_model = self.prefs.get("llm_model") or PROVIDERS[fb].default_model
        return fb, fb_model

    async def _run_with_fallback(self, primary_name: str, primary_model: str, op) -> Any:
        """Run `op()` with the primary provider; on LLMError, retry once with the fallback provider.

        The fallback is transient for this call: the user's persisted provider/model is never changed.
        """
        try:
            return await op()
        except LLMError as exc:
            fb = self._fallback()
            if fb is None:
                raise
            fb_name, fb_model = fb
            log.warning("LLM provider %s failed (%s); falling back to %s/%s for this request", primary_name, exc, fb_name, fb_model)
            prev = (self._provider, self._provider_name, self._model)
            try:
                self._apply(fb_name, fb_model, validate=False)
                return await op()
            finally:
                self._provider, self._provider_name, self._model = prev

    # ---- properties -------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self._provider is not None and self._provider.configured

    @property
    def provider(self) -> str | None:
        return self._provider_name

    @property
    def model(self) -> str | None:
        return self._model

    # ---- public API -------------------------------------------------------------
    async def text(self, system: str, user: str, images: list[bytes] | None = None, max_tokens: int = 2048) -> str:
        return await self._chat(system, user, images, False, max_tokens)

    async def structured(self, system: str, user: str, schema: type[T], images: list[bytes] | None = None,
                         retries: int = 2) -> T:
        """Ask for JSON matching `schema`; validate with pydantic and retry with the validation error."""
        full_schema = schema.model_json_schema()
        schema_json = compact_schema(full_schema)
        sys_prompt = f"{system}\n\nRespond with ONE JSON object only (no prose, no markdown) matching this JSON Schema:\n{schema_json}"
        prompt, last_err = user, ""
        for attempt in range(retries + 1):
            json_mode = attempt < retries or retries == 0
            try:
                raw = await self._chat(sys_prompt, prompt, images, full_schema if json_mode else False, 4096)
                return schema.model_validate(extract_json(raw))
            except LLMFormatError:
                last_err = "the reply was not valid JSON"
                log.warning("provider rejected malformed JSON (attempt %d)", attempt + 1)
                prompt = f"{user}\n\nYour previous reply was not valid JSON. Return ONE valid JSON object only."
            except (ValueError, ValidationError) as exc:
                last_err = str(exc)[:600]
                log.warning("structured output invalid (attempt %d): %s", attempt + 1, last_err)
                prompt = f"{user}\n\nYour previous reply was invalid: {last_err}\nReturn corrected JSON only."
        raise LLMError(f"Model did not return valid {schema.__name__}: {last_err}")

    async def _chat(self, system: str, user: str, images: list[bytes] | None, json_mode: bool | dict[str, Any],
                    max_tokens: int) -> str:
        if self._provider is None:
            raise LLMError("LLM provider is not available. Choose Ollama, Groq or Gemini in Settings.")
        if not self.configured:
            raise LLMError("LLM is not configured. Set the provider API key in .env.")
        model = (self.s.llm_vision_model or self._model) if images else self._model
        primary_name = self._provider_name or "provider"

        async def attempt() -> str:
            try:
                return await self._provider.chat(self._http, model, system, user, images or [], json_mode, max_tokens)
            except LLMError:
                raise
            except httpx.HTTPStatusError as exc:
                raise _normalize_http_error(exc.response, model or "", self._provider_name or "provider") from None
            except (httpx.ConnectError, httpx.ConnectTimeout):
                raise LLMUnreachable() from None
            except httpx.HTTPError as exc:
                raise LLMError(f"LLM network error: {type(exc).__name__}") from None

        return await self._run_with_fallback(primary_name, model, attempt)

    async def stream_text(self, system: str, user: str, max_tokens: int = 700) -> AsyncIterator[str]:
        """Yield text deltas as the model generates them (drives sentence-by-sentence speech)."""
        if self._provider is None:
            raise LLMError("LLM provider is not available. Choose Ollama, Groq or Gemini in Settings.")
        if not self.configured:
            raise LLMError("LLM is not configured. Set the provider API key in .env.")
        model = self._model
        primary_name = self._provider_name or "provider"
        yielded_any = False

        async def iterate(provider: LLMProvider, model_id: str) -> AsyncIterator[str]:
            nonlocal yielded_any
            async for chunk in provider.stream_chat(self._http, model_id, system, user, [], max_tokens):
                yielded_any = True
                yield chunk

        try:
            async for chunk in iterate(self._provider, model):
                yield chunk
            return
        except LLMError as exc:
            if yielded_any:
                raise
            fb = self._fallback()
            if fb is None:
                raise
            fb_name, fb_model = fb
            log.warning("LLM provider %s failed (%s); falling back to %s/%s for this request", primary_name, exc, fb_name, fb_model)
            prev = (self._provider, self._provider_name, self._model)
            try:
                self._apply(fb_name, fb_model, validate=False)
                async for chunk in iterate(self._provider, fb_model):
                    yield chunk
            finally:
                self._provider, self._provider_name, self._model = prev

    async def aclose(self) -> None:
        await self._http.aclose()


def _default_model_for(settings: Settings) -> str:
    try:
        return PROVIDERS[settings.llm_provider.lower()].default_model
    except Exception:  # noqa: BLE001
        return settings.llm_model or "llama3.2"

