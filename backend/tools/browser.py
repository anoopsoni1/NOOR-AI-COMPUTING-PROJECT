"""Playwright browser controller. DOM / accessibility locators first; coordinates only as a last resort.

All methods return ToolResult and never raise for expected failures (missing element, timeouts, HTTP errors).
"""
from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from backend.config import Settings
from backend.tools.base import ToolResult
from backend.tools.filesystem import known_folders

_CANDIDATES_JS = """() => {
  const sel = 'a,button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=combobox],[role=searchbox],[onclick],[contenteditable=true]';
  document.querySelectorAll('[data-nova-idx]').forEach(e => e.removeAttribute('data-nova-idx'));
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none') continue;
    const name = (el.getAttribute('aria-label') || el.innerText || el.placeholder || el.value || el.title || el.alt || el.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 100);
    el.setAttribute('data-nova-idx', String(i));
    out.push({idx: i, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '', type: el.type || '', name, href: el.href || ''});
    if (++i >= 300) break;
  }
  return out;
}"""

_LOGIN_URL = re.compile(r"/(log-?in|sign-?in|sso|auth(enticate)?|accounts/login|session/new)\b", re.I)
_SEARCH_SELECTORS = ["input[type=search]", "input[name=q]", "textarea[name=q]", "input[name=search]", "input[name=query]", "[role=searchbox]", "input[aria-label*=earch i]"]
def is_closed_error(exc: BaseException) -> bool:
    return "has been closed" in str(exc) or type(exc).__name__ == "TargetClosedError"


_ROLES = ("button", "link", "textbox", "searchbox", "combobox", "menuitem", "checkbox", "tab", "option", "radio")


def detect_blockers(url: str, title: str, text: str, has_password: bool, has_captcha: bool, dialog: str = "") -> list[str]:
    """Pure heuristic classification of 'unexpected state' pages. Returns blocker categories."""
    head = f"{title}\n{text[:300]}".lower()
    found: list[str] = []
    if has_captcha or re.search(r"verify (that )?you are (a )?human|captcha|unusual traffic", head):
        found.append("captcha")
    if has_password and (_LOGIN_URL.search(urlparse(url).path) or re.search(r"sign in|log in|login", head)):
        found.append("login_required")
    elif _LOGIN_URL.search(urlparse(url).path) and re.search(r"sign in|log in|login", head):
        found.append("login_required")
    if re.search(r"can.t be reached|err_[a-z_]+|dns_probe|no internet", head):
        found.append("network_error")
    if re.search(r"^404\b|page not found|not found\b.*404|404.*not found", head):
        found.append("not_found")
    if re.search(r"access denied|forbidden|403 error|not authorized", head):
        found.append("access_denied")
    if dialog:
        found.append("popup")
    return found


def _score(query: str, c: dict[str, Any]) -> float:
    q, name = query.lower().strip(), (c["name"] or "").lower()
    if not name:
        return 0.0
    score = 0.9 - min(0.3, abs(len(name) - len(q)) / 200) if q in name else 0.0
    score = max(score, SequenceMatcher(None, q, name).ratio())
    qt, nt = set(re.findall(r"\w+", q)), set(re.findall(r"\w+", name))
    if qt:
        score = max(score, 0.85 * len(qt & nt) / len(qt) - 0.02 * max(0, len(nt) - len(qt)))
    if "search" in q and (c["type"] == "search" or "search" in name or c["role"] == "searchbox"):
        score = max(score, 0.8)
    return round(score, 3)


class BrowserManager:
    def __init__(self, settings: Settings, headless: bool | None = None) -> None:
        self.s = settings
        self.headless = settings.browser_headless if headless is None else headless
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self.last_dialog: str = ""
        self.last_url: str = ""

    # ---- lifecycle -------------------------------------------------------------------
    async def page(self) -> Any:
        """Current page. If the browser window was closed (by the user or a crash) it is relaunched transparently."""
        async with self._lock:
            for attempt in range(2):
                try:
                    return await self._ensure_page()
                except Exception as exc:  # noqa: BLE001
                    if attempt == 1 or not is_closed_error(exc):
                        raise
                    await self._reset()
            raise RuntimeError("unreachable")

    async def _ensure_page(self) -> Any:
        if self._ctx is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            kwargs: dict[str, Any] = dict(headless=self.headless, accept_downloads=True, viewport={"width": 1280, "height": 800},
                                          args=["--disable-blink-features=AutomationControlled"])
            self.s.browser_profile_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._ctx = await self._pw.chromium.launch_persistent_context(
                    str(self.s.browser_profile_dir), channel=self.s.browser_channel or None, **kwargs)
            except Exception:  # noqa: BLE001 - channel (e.g. Chrome) not installed -> bundled Chromium
                self._ctx = await self._pw.chromium.launch_persistent_context(str(self.s.browser_profile_dir), **kwargs)
            self._ctx.on("page", self._attach)  # popups / target=_blank tabs become the active page
            self._ctx.on("close", lambda *_: self._forget())  # window closed by the user -> relaunch next time
            for p in self._ctx.pages:
                self._attach(p)
        if self._page is None or self._page.is_closed():
            pages = [p for p in self._ctx.pages if not p.is_closed()]
            self._page = pages[-1] if pages else await self._ctx.new_page()
            self._attach(self._page)
        return self._page

    def _forget(self) -> None:
        self._ctx, self._page = None, None

    async def _reset(self) -> None:
        """Drop a dead browser so the next call starts a fresh one."""
        ctx, pw = self._ctx, self._pw
        self._ctx, self._page, self._pw = None, None, None
        for closer in (getattr(ctx, "close", None), getattr(pw, "stop", None)):
            if closer:
                try:
                    await asyncio.wait_for(closer(), 5)
                except Exception:  # noqa: BLE001
                    pass

    def _attach(self, page: Any) -> None:
        self._page = page
        if getattr(page, "_nova_attached", False):
            return
        page._nova_attached = True

        async def on_dialog(d: Any) -> None:
            self.last_dialog = f"{d.type}: {d.message[:200]}"
            await d.dismiss()

        page.on("dialog", lambda d: asyncio.create_task(on_dialog(d)))

    @property
    def is_open(self) -> bool:
        return self._ctx is not None and self._page is not None and not self._page.is_closed()

    async def close(self) -> None:
        async with self._lock:
            ctx, pw, self._ctx, self._page, self._pw = self._ctx, self._pw, None, None, None
        for closer in (getattr(ctx, "close", None), getattr(pw, "stop", None)):
            if closer:
                try:
                    await asyncio.wait_for(closer(), 8)
                except Exception:  # noqa: BLE001
                    pass

    async def _settle(self, page: Any, timeout: int = 4000) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        self.last_url = page.url

    # ---- navigation ---------------------------------------------------------------------------
    async def open_url(self, url: str) -> ToolResult:
        if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
            url = "https://" + url
        if urlparse(url).scheme not in ("http", "https"):
            return ToolResult.fail("open_url", "Only http(s) URLs are allowed", "invalid_url", target=url)
        page = await self.page()
        self.last_dialog = ""
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "chrome-error://" in msg:  # server answered with an error status and an empty body
                return ToolResult.fail("open_url", f"{url} returned an error page (HTTP error or unreachable)", "http_error", target=url)
            if "net::ERR" in msg or "NS_ERROR" in msg:
                code = "network_error"
            elif "Timeout" in msg:
                code = "timeout"
            else:
                code = "navigation_failed"
            return ToolResult.fail("open_url", f"Could not open {url}: {msg.splitlines()[0][:160]}", code, target=url)
        await self._settle(page)
        status = resp.status if resp else 0
        data = {"url": page.url, "title": await page.title(), "status": status}
        if status in (401,):
            return ToolResult.fail("open_url", f"{url} requires authentication (HTTP 401)", "login_required", target=url, **data)
        if status == 403:
            return ToolResult.fail("open_url", f"Access denied (HTTP 403) for {url}", "access_denied", target=url, **data)
        if status == 404:
            return ToolResult.fail("open_url", f"Page not found (HTTP 404): {url}", "not_found", target=url, **data)
        if status >= 400:
            return ToolResult.fail("open_url", f"HTTP {status} from {url}", "http_error", target=url, **data)
        return ToolResult.ok("open_url", url, f"Opened {data['title'] or url}", **data)

    # ---- element location -------------------------------------------------------------------------
    async def candidates(self, description: str, limit: int = 8) -> list[dict[str, Any]]:
        page = await self.page()
        try:
            items = await page.evaluate(_CANDIDATES_JS)
        except Exception:  # noqa: BLE001 - page navigating
            await self._settle(page)
            items = await page.evaluate(_CANDIDATES_JS)
        for c in items:
            c["score"] = _score(description, c)
        return sorted((c for c in items if c["score"] > 0.3), key=lambda c: -c["score"])[:limit]

    async def _locate(self, target: str, selector: str, role: str, timeout_ms: int = 5000) -> tuple[Any, str] | None:
        page = await self.page()
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while True:
            strategies: list[tuple[str, Any]] = []
            if selector:
                strategies.append(("selector", page.locator(selector)))
            if target:
                rx = re.compile(re.escape(target), re.I)
                if role:
                    strategies.append(("role", page.get_by_role(role, name=rx)))  # type: ignore[arg-type]
                strategies += [("role", page.get_by_role(r, name=rx)) for r in _ROLES]  # type: ignore[arg-type]
                strategies += [("label", page.get_by_label(rx)), ("placeholder", page.get_by_placeholder(rx)),
                               ("text", page.get_by_text(rx)), ("title", page.get_by_title(rx)), ("alt", page.get_by_alt_text(rx))]
                if "search" in target.lower():
                    strategies += [("search", page.locator(s)) for s in _SEARCH_SELECTORS]
            for how, loc in strategies:
                try:
                    n = await loc.count()
                    for i in range(min(n, 5)):
                        el = loc.nth(i)
                        if await el.is_visible():
                            return el, how
                except Exception:  # noqa: BLE001
                    continue
            if asyncio.get_event_loop().time() >= deadline:
                break
            await asyncio.sleep(0.2)
        # fuzzy fallback over every interactive element on the page
        if target:
            cands = await self.candidates(target, 3)
            if cands and cands[0]["score"] >= 0.6:
                return page.locator(f'[data-nova-idx="{cands[0]["idx"]}"]'), f"fuzzy:{cands[0]['score']}"
        return None

    async def find_element(self, description: str) -> ToolResult:
        cands = await self.candidates(description)
        if not cands:
            return ToolResult.fail("find_element", f"No element resembling '{description}'", "element_not_found", target=description)
        best = cands[0]
        return ToolResult.ok("find_element", description, f"Best match: {best['tag']} '{best['name']}' (score {best['score']})",
                             matches=[{k: v for k, v in c.items() if k != "idx"} for c in cands])

    # ---- interaction ----------------------------------------------------------------------------------
    async def click(self, target: str, selector: str = "", role: str = "") -> ToolResult:
        found = await self._locate(target, selector, role)
        label = target or selector
        if not found:
            return ToolResult.fail("click", f"Could not find '{label}' on the page", "element_not_found", target=label)
        el, how = found
        page = await self.page()
        before = page.url
        try:
            await el.scroll_into_view_if_needed(timeout=3000)
            await el.click(timeout=6000)
        except Exception as exc:  # noqa: BLE001
            code = "timeout" if "Timeout" in str(exc) else "click_failed"
            return ToolResult.fail("click", f"Click on '{label}' failed: {str(exc).splitlines()[0][:140]}", code, target=label)
        await asyncio.sleep(0.4)
        await self._settle(page)
        return ToolResult.ok("click", label, f"Clicked {label}", located_by=how, navigated=page.url != before, url=page.url)

    async def click_at(self, x: int, y: int, label: str = "") -> ToolResult:
        page = await self.page()
        await page.mouse.click(x, y)
        await asyncio.sleep(0.5)
        await self._settle(page)
        return ToolResult.ok("click", label or f"({x},{y})", f"Clicked {label or 'position'} (visual)", via="vision", url=page.url)

    async def type_text(self, text: str, target: str = "", selector: str = "", submit: bool = False, clear: bool = True) -> ToolResult:
        page = await self.page()
        label = target or selector or "focused element"
        if target or selector:
            found = await self._locate(target, selector, "")
            if not found:
                return ToolResult.fail("type_text", f"Could not find input '{label}'", "element_not_found", target=label)
            el, _ = found
            try:
                meta = await el.evaluate("e => ({t: (e.type||'').toLowerCase(), n: ((e.name||'')+(e.id||'')+(e.autocomplete||'')).toLowerCase()})")
                if meta["t"] == "password" or re.search(r"pass(word|wd)|passcode|cvv|card.?number", meta["n"]):
                    return ToolResult.fail("type_text", "Refusing to type into a password/credential field", "credentials_blocked", target=label)
                await el.click(timeout=4000)
                if clear:
                    await el.fill(text, timeout=5000)
                else:
                    await el.press_sequentially(text, delay=15)
            except Exception as exc:  # noqa: BLE001
                return ToolResult.fail("type_text", f"Typing into '{label}' failed: {str(exc).splitlines()[0][:140]}", "type_failed", target=label)
            if submit:
                await el.press("Enter")
        else:
            await page.keyboard.type(text, delay=15)
            if submit:
                await page.keyboard.press("Enter")
        await asyncio.sleep(0.4)
        await self._settle(page)
        return ToolResult.ok("type_text", label, f"Typed {len(text)} chars into {label}" + (" and submitted" if submit else ""), url=page.url)

    async def press_key(self, key: str) -> ToolResult:
        page = await self.page()
        try:
            await page.keyboard.press(key)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("press_key", f"Invalid key '{key}': {str(exc).splitlines()[0][:100]}", "invalid_key", target=key)
        await asyncio.sleep(0.3)
        await self._settle(page, 2000)
        return ToolResult.ok("press_key", key, f"Pressed {key}", url=page.url)

    async def scroll(self, direction: str, amount: int) -> ToolResult:
        page = await self.page()
        if direction == "top":
            await page.evaluate("window.scrollTo(0,0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0,document.body.scrollHeight)")
        else:
            await page.mouse.wheel(0, amount if direction == "down" else -amount)
        await asyncio.sleep(0.3)
        return ToolResult.ok("scroll", direction, f"Scrolled {direction}")

    async def wait(self, seconds: float, for_text: str = "", for_selector: str = "") -> ToolResult:
        page = await self.page() if (for_text or for_selector) else None
        try:
            if for_selector:
                await page.wait_for_selector(for_selector, timeout=seconds * 1000)  # type: ignore[union-attr]
            elif for_text:
                await page.get_by_text(for_text).first.wait_for(timeout=seconds * 1000)  # type: ignore[union-attr]
            else:
                await asyncio.sleep(seconds)
        except Exception:  # noqa: BLE001
            return ToolResult.fail("wait", f"Timed out after {seconds}s waiting for '{for_text or for_selector}'", "timeout", target=for_text or for_selector)
        return ToolResult.ok("wait", for_text or for_selector, f"Waited {seconds}s")

    # ---- reading -----------------------------------------------------------------------------------------
    async def snapshot(self, text_limit: int = 1500, links: bool = False) -> dict[str, Any]:
        """Cheap observation of the current page used by the verifier and conversation context."""
        if not self.is_open:
            return {}
        page = self._page
        page_links: list[dict[str, str]] = []
        try:
            title = await page.title()
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            has_pw = await page.locator("input[type=password]:visible").count() > 0
            has_captcha = await page.locator("iframe[src*=captcha i], iframe[src*=recaptcha i], iframe[src*=hcaptcha i], [id*=captcha i]:visible").count() > 0
            if links:
                try:
                    page_links = await page.evaluate(
                        "() => [...document.querySelectorAll('a[href]')].slice(0, 30).map(a => ({text: a.innerText.trim().slice(0, 80), href: a.href})).filter(l => l.text)"
                    )
                except Exception:
                    page_links = []
        except Exception:  # noqa: BLE001 - mid-navigation
            return {"url": page.url, "title": "", "text": "", "blockers": [], "links": []}
        dialog, self.last_dialog = self.last_dialog, ""
        return {"url": page.url, "title": title, "text": text[:text_limit],
                "blockers": detect_blockers(page.url, title, text, has_pw, has_captcha, dialog), "dialog": dialog, "links": page_links}

    async def read_page(self, max_chars: int) -> ToolResult:
        page = await self.page()
        await self._settle(page)
        try:
            data = await page.evaluate("""() => ({
              text: document.body ? document.body.innerText : '',
              headings: [...document.querySelectorAll('h1,h2,h3')].slice(0, 25).map(h => h.innerText.trim()).filter(Boolean),
              links: [...document.querySelectorAll('a[href]')].slice(0, 40).map(a => ({text: a.innerText.trim().slice(0, 80), href: a.href})).filter(l => l.text)
            })""")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("read_page", f"Could not read page: {str(exc).splitlines()[0][:120]}", "read_failed")
        text = re.sub(r"\n{3,}", "\n\n", data["text"]).strip()
        return ToolResult.ok("read_page", page.url, f"Read '{await page.title()}' ({len(text)} chars)", url=page.url, title=await page.title(),
                             text=text[:max_chars], truncated=len(text) > max_chars, headings=data["headings"], links=data["links"])

    async def screenshot(self, path: Path) -> tuple[int, int]:
        page = await self.page()
        await page.screenshot(path=str(path))
        vp = page.viewport_size or {"width": 1280, "height": 800}
        return vp["width"], vp["height"]

    # ---- files ----------------------------------------------------------------------------------------------
    async def download(self, url: str = "", target: str = "", selector: str = "", save_dir: str = "", filename: str = "") -> ToolResult:
        dest_dir = Path(save_dir) if save_dir else known_folders()["downloads"] / "NOVA"
        dest_dir.mkdir(parents=True, exist_ok=True)
        page = await self.page()
        try:
            if url:
                resp = await page.context.request.get(url, timeout=60000)
                if not resp.ok:
                    return ToolResult.fail("download_file", f"Download failed: HTTP {resp.status}", "download_failed", target=url)
                name = filename or unquote(Path(urlparse(url).path).name) or "download"
                dest = dest_dir / re.sub(r'[<>:"/\\|?*]', "_", name)
                dest.write_bytes(await resp.body())
            else:
                found = await self._locate(target, selector, "")
                if not found:
                    return ToolResult.fail("download_file", f"Could not find '{target or selector}'", "element_not_found", target=target)
                async with page.expect_download(timeout=45000) as info:
                    await found[0].click(timeout=6000)
                dl = await info.value
                dest = dest_dir / re.sub(r'[<>:"/\\|?*]', "_", filename or dl.suggested_filename)
                await dl.save_as(str(dest))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("download_file", f"Download failed: {str(exc).splitlines()[0][:140]}", "download_failed", target=url or target)
        if not dest.exists() or dest.stat().st_size == 0:
            return ToolResult.fail("download_file", "Download produced an empty/missing file", "download_failed", target=str(dest))
        return ToolResult.ok("download_file", str(dest), f"Downloaded {dest.name} ({dest.stat().st_size} bytes)", path=str(dest), bytes=dest.stat().st_size)

    async def upload(self, path: Path, target: str = "", selector: str = "") -> ToolResult:
        page = await self.page()
        loc = page.locator(selector or "input[type=file]").first
        try:
            await loc.set_input_files(str(path), timeout=6000)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("upload_file", f"Upload failed: {str(exc).splitlines()[0][:140]}", "upload_failed", target=target)
        return ToolResult.ok("upload_file", path.name, f"Attached {path.name}")
