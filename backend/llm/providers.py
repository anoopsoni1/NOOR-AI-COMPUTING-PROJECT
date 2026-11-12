"""LLM provider abstraction: Ollama, Groq and Google Gemini.

Every provider implements the same small interface so the rest of NOOR
(conversation, planner, executor, graph) can switch providers at runtime
without knowing which cloud or local backend is behind the response.

Providers never log or return API keys. All errors are normalised into
:class:`LLMError` subclasses with actionable, user-safe messages.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger("noor.llm.providers")


# --------------------------------------------------------------------------- errors
class LLMError(RuntimeError):
    """Base provider error."""


class LLMFormatError(LLMError):
    """The provider rejected the reply as malformed JSON (`json_validate_failed`). Safe to retry."""


class LLMAuthenticationError(LLMError):
    """Missing or invalid API key / cannot authenticate."""


class LLMRateLimitError(LLMError):
    """Provider rate-limited the request (HTTP 429)."""


class LLMNotFoundError(LLMError):
    """Requested model is not available on the provider."""


class LLMUnavailableError(LLMError):
    """Provider cannot be reached."""


@dataclass
class ProviderModel:
    id: str
    label: str | None = None


# --------------------------------------------------------------------------- shared helpers
_MODEL_NOT_FOUND = re.compile(r"(not found|does not have|model.*not found)", re.I)


def _normalize_http_error(resp: httpx.Response, model: str, provider: str) -> LLMError:
    """Turn an HTTP error response into a safe, actionable LLMError (no keys leaked)."""
    status = resp.status_code
    body = (resp.text or "")[:400]
    if status in (401, 403):
        return LLMAuthenticationError(
            f"{provider}: authentication failed. Check that the API key is set and valid (keys are never shown in the UI)."
        )
    if status == 429:
        return LLMRateLimitError(f"{provider}: rate limited. Please try again shortly.")
    if status == 404 or _MODEL_NOT_FOUND.search(body):
        return LLMNotFoundError(f"{provider}: model '{model}' is not available. Pick another model or install it.")
    if status >= 500:
        return LLMUnavailableError(f"{provider}: server error (HTTP {status}). Please try again later.")
    return LLMError(f"{provider}: HTTP {status}: {body[:200]}")


def _retry_after_seconds(resp: httpx.Response) -> float:
    try:
        if "retry-after" in resp.headers:
            return float(resp.headers["retry-after"])
    except ValueError:
        pass
    m = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", resp.text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 5.0


async def _post_retry(http: httpx.AsyncClient, url: str, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    """POST with automatic back-off on rate limits (HTTP 429)."""
    import asyncio

    last: httpx.Response | None = None
    for attempt in range(4):
        r = await http.post(url, json=body, headers=headers)
        last = r
        if r.status_code == 429 and attempt < 3:
            wait = min(_retry_after_seconds(r) + 0.5, 30.0)
            log.warning("%s rate limited; retrying in %.1fs", url, wait)
            await asyncio.sleep(wait)
            continue
        r.raise_for_status()
        return r
    assert last is not None
    raise last  # type: ignore[return-value]


async def _stream_retry(http: httpx.AsyncClient, method: str, url: str, headers: dict[str, str],
                        json_body: dict[str, Any]) -> httpx.Response:
    """Open a streaming request with automatic back-off on rate limits (HTTP 429).

    The response is handed back open so the caller can iterate ``aiter_lines()``;
    the caller MUST close it with ``await r.aclose()`` when done.
    """
    import asyncio

    last: httpx.Response | None = None
    for attempt in range(4):
        req = http.build_request(method, url, headers=headers, json=json_body)
        r = await http.send(req, stream=True)
        last = r
        try:
            if r.status_code == 429 and attempt < 3:
                wait = min(_retry_after_seconds(r) + 0.5, 30.0)
                log.warning("%s rate limited; retrying in %.1fs", url, wait)
                await r.aclose()
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 400:
                await r.aread()
                await r.aclose()
                raise _normalize_http_error(r, str(json_body.get("model", "")), "provider") from None
            return r  # caller consumes and closes the stream with await r.aclose()
        except Exception:
            await r.aclose()
            raise
    assert last is not None
    raise last  # type: ignore[return-value]


# --------------------------------------------------------------------------- providers
class LLMProvider(ABC):
    name = "provider"
    display_name = "Provider"
    needs_api_key = False
    default_model = ""
    default_base_url = ""

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @abstractmethod
    async def list_models(self, http: httpx.AsyncClient) -> list[ProviderModel]:
        ...

    @abstractmethod
    async def validate(self, http: httpx.AsyncClient) -> None:
        ...

    @abstractmethod
    async def chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                   images: list[bytes], json_mode: bool | dict[str, Any], max_tokens: int) -> str:
        ...

    @abstractmethod
    async def stream_chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                          images: list[bytes], max_tokens: int) -> AsyncIterator[str]:
        ...

    @property
    def configured(self) -> bool:
        if not self.needs_api_key:
            return True
        return bool(self.api_key)


class OllamaProvider(LLMProvider):
    name = "ollama"
    display_name = "Ollama (local)"
    needs_api_key = False
    default_model = "llama3.2"
    default_base_url = "http://localhost:11434"

    async def list_models(self, http: httpx.AsyncClient) -> list[ProviderModel]:
        r = await http.get(f"{self.base_url}/api/tags")
        r.raise_for_status()
        out: list[ProviderModel] = []
        for m in r.json().get("models", []):
            name = m.get("name") or ""
            if name:
                out.append(ProviderModel(id=name, label=name))
        return out

    async def validate(self, http: httpx.AsyncClient) -> None:
        try:
            r = await http.get(f"{self.base_url}/api/tags")
            r.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Ollama is not running. Start Ollama and make sure it is available at http://localhost:11434.") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(f"Ollama unreachable: {type(exc).__name__}") from None

    async def chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                   images: list[bytes], json_mode: bool | dict[str, Any], max_tokens: int) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image", "image": base64.b64encode(i).decode()} for i in images
            ]
        messages.append({"role": "user", "content": content, **( {"images": [base64.b64encode(i).decode() for i in images]} if images else {} )})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": "60m",
            "options": {"num_ctx": 4096},
        }
        if isinstance(json_mode, dict):
            body["format"] = json_mode
            body["options"]["temperature"] = 0.1
        elif json_mode is True:
            body["format"] = "json"
        try:
            r = await _post_retry(http, f"{self.base_url}/api/chat", body, {})
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, model, "Ollama") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Ollama is not running. Start Ollama at http://localhost:11434.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama network error: {type(exc).__name__}") from None
        return (r.json().get("message") or {}).get("content") or ""

    async def stream_chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                          images: list[bytes], max_tokens: int) -> AsyncIterator[str]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}]
        messages.append({"role": "user", "content": content, **( {"images": [base64.b64encode(i).decode() for i in images]} if images else {} )})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "60m",
            "options": {"num_ctx": 4096},
        }
        try:
            r = await _stream_retry(http, "POST", f"{self.base_url}/api/chat", {}, body)
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, model, "Ollama") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Ollama is not running.") from exc
        try:
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("done"):
                    break
                delta = (ev.get("message") or {}).get("content") or ""
                if delta:
                    yield delta
        finally:
            await r.aclose()


class GroqProvider(LLMProvider):
    name = "groq"
    display_name = "Groq (cloud)"
    needs_api_key = True
    default_model = "openai/gpt-oss-20b"
    default_base_url = "https://api.groq.com/openai/v1"

    # Groq models that are not text-generation chat models.
    _NON_TEXT = re.compile(r"(whisper|tts|enrich|embed)", re.I)

    async def list_models(self, http: httpx.AsyncClient) -> list[ProviderModel]:
        if not self.api_key:
            return []
        try:
            r = await http.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
            out: list[ProviderModel] = []
            for m in r.json().get("data", []):
                mid = m.get("id") or ""
                if mid and not self._NON_TEXT.search(mid):
                    out.append(ProviderModel(id=mid, label=mid))
            return out
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LLMAuthenticationError("Groq: invalid API key. Check GROQ_API_KEY in .env.") from None
            raise _normalize_http_error(exc.response, "", "Groq") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Groq. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq network error: {type(exc).__name__}") from None

    async def validate(self, http: httpx.AsyncClient) -> None:
        if not self.api_key:
            raise LLMAuthenticationError("Groq: API key missing — set GROQ_API_KEY in .env (never stored in the UI).")
        try:
            r = await http.get(f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"})
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise LLMAuthenticationError("Groq: invalid API key. Check GROQ_API_KEY in .env.") from None
            raise _normalize_http_error(exc.response, "", "Groq") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Groq. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq network error: {type(exc).__name__}") from None

    async def chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                   images: list[bytes], json_mode: bool | dict[str, Any], max_tokens: int) -> str:
        if not self.api_key:
            raise LLMAuthenticationError("Groq: API key missing — set GROQ_API_KEY in .env.")
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()}} for i in images
            ]
        body: dict[str, Any] = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            if not any("json" in str(m.get("content", "")).lower() for m in body["messages"]):
                body["messages"][0]["content"] += "\nRespond with valid JSON." 
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = await _post_retry(http, f"{self.base_url}/chat/completions", body, headers)
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, model, "Groq") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Groq. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Groq network error: {type(exc).__name__}") from None
        return r.json()["choices"][0]["message"]["content"] or ""

    async def stream_chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                          images: list[bytes], max_tokens: int) -> AsyncIterator[str]:
        if not self.api_key:
            raise LLMAuthenticationError("Groq: API key missing — set GROQ_API_KEY in .env.")
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()}} for i in images
            ]
        body: dict[str, Any] = {"model": model, "stream": True,
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = await _stream_retry(http, "POST", f"{self.base_url}/chat/completions", headers, body)
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, model, "Groq") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Groq. Check your network connection.") from exc
        try:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                delta = (ev.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                if delta:
                    yield delta
        finally:
            await r.aclose()


class GeminiProvider(LLMProvider):
    name = "gemini"
    display_name = "Google Gemini (cloud)"
    needs_api_key = True
    default_model = "gemini-3.5-flash"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    _NON_TEXT = re.compile(r"(embedding|rerank|imagen|tts|audio)", re.I)
    _DEPRECATED = re.compile(r"^(chat-|text-|embedding-|gemini-1|gemini-2|gemma|learnlm)", re.I)

    def _key_url(self, path: str) -> str:
        separator = "&" if "?" in path else "?"
        return f"{self.base_url}{path}{separator}key={self.api_key}"

    async def list_models(self, http: httpx.AsyncClient) -> list[ProviderModel]:
        if not self.api_key:
            return []
        try:
            r = await http.get(self._key_url("/models"))
            r.raise_for_status()
            out: list[ProviderModel] = []
            for m in r.json().get("models", []):
                name = m.get("name", "")
                if not name:
                    continue
                methods = m.get("supportedGenerationMethods") or []
                if "generateContent" not in methods:
                    continue
                mid = name[len("models/"):] if name.startswith("models/") else name
                if self._NON_TEXT.search(mid) or self._DEPRECATED.search(mid):
                    continue
                out.append(ProviderModel(id=mid, label=mid))
            if not out:
                out = [
                    ProviderModel(id="gemini-3.5-flash", label="gemini-3.5-flash"),
                    ProviderModel(id="gemini-3.6-flash", label="gemini-3.6-flash"),
                    ProviderModel(id="gemini-3-flash-preview", label="gemini-3-flash-preview"),
                ]
            return out
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 401, 403):
                raise LLMAuthenticationError("Google Gemini: invalid API key. Check GEMINI_API_KEY in .env.") from None
            raise _normalize_http_error(exc.response, "", "Gemini") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Google Gemini. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini network error: {type(exc).__name__}") from None

    async def validate(self, http: httpx.AsyncClient) -> None:
        if not self.api_key:
            raise LLMAuthenticationError("Google Gemini: API key missing — set GEMINI_API_KEY in .env (never stored in the UI).")
        try:
            r = await http.get(self._key_url("/models"))
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (400, 401, 403):
                raise LLMAuthenticationError("Google Gemini: invalid API key. Check GEMINI_API_KEY in .env.") from None
            raise _normalize_http_error(exc.response, "", "Gemini") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Google Gemini. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini network error: {type(exc).__name__}") from None

    async def chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                   images: list[bytes], json_mode: bool | dict[str, Any], max_tokens: int) -> str:
        effective_model = "gemini-3.5-flash" if self._DEPRECATED.search(model) else model
        parts: list[dict[str, Any]] = [{"text": user}]
        for i in images:
            parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(i).decode()}})
        output_limit = max(max_tokens, 2048)
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": output_limit},
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = await _post_retry(http, self._key_url(f"/models/{effective_model}:generateContent"), body, {})
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, effective_model, "Gemini") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Google Gemini. Check your network connection.") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini network error: {type(exc).__name__}") from None
        try:
            cand = (r.json().get("candidates") or [{}])[0]
            cand_parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in cand_parts if p.get("text"))
            if not text and cand.get("finishReason") in ("SAFETY", "RECITATION"):
                raise LLMError(f"Gemini response blocked: {cand.get('finishReason')}")
            return text
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Gemini: unexpected response format") from exc

    async def stream_chat(self, http: httpx.AsyncClient, model: str, system: str, user: str,
                          images: list[bytes], max_tokens: int) -> AsyncIterator[str]:
        effective_model = "gemini-3.5-flash" if self._DEPRECATED.search(model) else model
        parts: list[dict[str, Any]] = [{"text": user}]
        for i in images:
            parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(i).decode()}})
        output_limit = max(max_tokens, 2048)
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": output_limit},
        }
        headers = {"Content-Type": "application/json"}
        try:
            r = await _stream_retry(http, "POST", self._key_url(f"/models/{effective_model}:streamGenerateContent"), headers, body)
        except httpx.HTTPStatusError as exc:
            raise _normalize_http_error(exc.response, effective_model, "Gemini") from None
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailableError("Cannot reach Google Gemini. Check your network connection.") from exc
        try:
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                for cand in ev.get("candidates") or []:
                    for p in cand.get("content", {}).get("parts", []):
                        delta = p.get("text", "")
                        if delta:
                            yield delta
        finally:
            await r.aclose()


PROVIDERS: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
}


def build_provider(settings: Any, provider_name: str | None = None, model: str | None = None) -> LLMProvider:
    """Build a provider from settings, honouring runtime overrides."""
    name = (provider_name or settings.llm_provider).lower()
    if name not in PROVIDERS:
        raise LLMError(f"Unsupported LLM provider '{name}'. Use one of {sorted(PROVIDERS)}")
    cls = PROVIDERS[name]
    base_url, default_model = _resolve_base_url(settings, name)
    model_id = model or settings.llm_model or default_model
    return cls(base_url, _api_key_for(settings, name))


def _resolve_base_url(settings: Any, name: str) -> tuple[str, str]:
    cls = PROVIDERS[name]
    custom = getattr(settings, "llm_base_url", "") or ""
    if name == "ollama":
        base = getattr(settings, "ollama_host", None) or (custom if "11434" in custom else "") or cls.default_base_url
    elif name == "groq":
        base = custom if (custom and "11434" not in custom and "googleapis" not in custom) else cls.default_base_url
    elif name == "gemini":
        base = custom if (custom and "11434" not in custom and "groq.com" not in custom) else cls.default_base_url
    else:
        base = cls.default_base_url
    return base, cls.default_model


def _read_env_value(key: str) -> str:
    """Read a key from os.environ or .env files directly (enables hot-reloading when user saves .env without restart)."""
    import os
    from pathlib import Path

    val = os.environ.get(key, "").strip()
    if val:
        return val
    root = Path(__file__).resolve().parents[2]
    # Check both backend/.env and root .env (root .env has higher precedence)
    for candidate in (root / "backend" / ".env", root / ".env"):
        if candidate.is_file():
            try:
                for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == key:
                        v_str = v.strip().strip("'\"")
                        if v_str:
                            val = v_str
            except Exception:
                pass
    return val


def _api_key_for(settings: Any, name: str) -> str:
    if name == "groq":
        k = settings.groq_api_key.get_secret_value() if hasattr(settings, "groq_api_key") else ""
        return k or _read_env_value("GROQ_API_KEY")
    if name == "gemini":
        k = settings.gemini_api_key.get_secret_value() if hasattr(settings, "gemini_api_key") else ""
        return k or _read_env_value("GEMINI_API_KEY")
    k = settings.llm_api_key.get_secret_value() if hasattr(settings, "llm_api_key") else ""
    return k or _read_env_value("LLM_API_KEY")

