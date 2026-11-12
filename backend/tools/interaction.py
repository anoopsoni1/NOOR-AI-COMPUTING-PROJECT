"""Generic interaction tools. `mode` selects the surface: "browser" (Playwright/DOM, default) or
"desktop" (PyAutoGUI + vision). Desktop mode is only used when the browser cannot do the job."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.tools.base import ToolContext, ToolResult, tool
from backend.tools.filesystem import resolve_path
from backend.vision.screen_analyzer import to_pixels

Mode = Literal["browser", "desktop"]


# ---- helpers ---------------------------------------------------------------------------------------------
async def _desktop_locate(ctx: ToolContext, description: str) -> tuple[int, int] | None:
    if not (ctx.analyzer and ctx.analyzer.available and ctx.settings.vision_enabled):
        return None
    path = ctx.screenshot_path("locate")
    w, h = await ctx.computer.screenshot(path)
    el = await ctx.analyzer.locate(path, description)
    return to_pixels(el, w, h) if el else None


async def _browser_visual_click(ctx: ToolContext, description: str) -> ToolResult | None:
    """Last-resort: ask the vision model where the element is in the page screenshot."""
    if not (description and ctx.analyzer and ctx.analyzer.available and ctx.settings.vision_enabled):
        return None
    path = ctx.screenshot_path("visual_fallback")
    w, h = await ctx.browser.screenshot(path)
    el = await ctx.analyzer.locate(path, description)
    if not el:
        return None
    x, y = to_pixels(el, w, h)
    return await ctx.browser.click_at(x, y, description)


# ---- open_url -------------------------------------------------------------------------------------------------
class OpenUrlArgs(BaseModel):
    url: str = Field(description="full URL or domain; this is also how to 'open Chrome'/'open a website'")


@tool("open_url", "Open a URL in the automation browser", OpenUrlArgs, "browser")
async def open_url(ctx: ToolContext, a: OpenUrlArgs) -> ToolResult:
    return await ctx.browser.open_url(a.url)


# ---- click ---------------------------------------------------------------------------------------------------------
class ClickArgs(BaseModel):
    target: str = Field("", description="human description / visible text of the element, e.g. 'Sign in button'")
    selector: str = Field("", description="optional CSS selector if known")
    role: str = Field("", description="optional ARIA role: button, link, tab...")
    x: Optional[int] = Field(None, description="desktop mode only: pixel x")
    y: Optional[int] = Field(None, description="desktop mode only: pixel y")
    mode: Mode = "browser"


@tool("click", "Click an element (browser: by text/role/selector; desktop: by coordinates or vision lookup)", ClickArgs, "browser")
async def click(ctx: ToolContext, a: ClickArgs) -> ToolResult:
    if a.mode == "desktop":
        pos = (a.x, a.y) if a.x is not None and a.y is not None else await _desktop_locate(ctx, a.target)
        if pos is None:
            return ToolResult.fail("click", f"Could not locate '{a.target}' on screen", "element_not_found", target=a.target)
        await ctx.computer.click(*pos)
        return ToolResult.ok("click", a.target or f"{pos}", f"Clicked {a.target or pos} at {pos}", x=pos[0], y=pos[1])
    if not (a.target or a.selector):
        return ToolResult.fail("click", "click needs a target or selector", "invalid_args")
    res = await ctx.browser.click(a.target, a.selector, a.role)
    if res.error_code == "element_not_found":
        visual = await _browser_visual_click(ctx, a.target)
        if visual:
            return visual
    return res


# ---- type_text -------------------------------------------------------------------------------------------------------
class TypeTextArgs(BaseModel):
    text: str
    target: str = Field("", description="input to type into, e.g. 'search box'; empty = currently focused element")
    selector: str = ""
    submit: bool = Field(False, description="press Enter afterwards (e.g. to run a search)")
    clear: bool = True
    mode: Mode = "browser"


@tool("type_text", "Type text into an input (never into password fields)", TypeTextArgs, "browser")
async def type_text(ctx: ToolContext, a: TypeTextArgs) -> ToolResult:
    if a.mode == "desktop":
        await ctx.computer.type(a.text)
        if a.submit:
            await ctx.computer.press("enter")
        return ToolResult.ok("type_text", a.target or "focused window", f"Typed {len(a.text)} chars")
    return await ctx.browser.type_text(a.text, a.target, a.selector, a.submit, a.clear)


# ---- press_key ------------------------------------------------------------------------------------------------------------
class PressKeyArgs(BaseModel):
    key: str = Field(description="e.g. 'Enter', 'Escape', 'Tab'; combos like 'Control+L' (browser) or 'ctrl+s' (desktop)")
    mode: Mode = "browser"


@tool("press_key", "Press a key or key combination", PressKeyArgs, "browser")
async def press_key(ctx: ToolContext, a: PressKeyArgs) -> ToolResult:
    if a.mode == "desktop":
        keys = [k.strip() for k in a.key.split("+") if k.strip()]
        await (ctx.computer.hotkey(*keys) if len(keys) > 1 else ctx.computer.press(keys[0]))
        return ToolResult.ok("press_key", a.key, f"Pressed {a.key}")
    return await ctx.browser.press_key(a.key)


# ---- scroll ----------------------------------------------------------------------------------------------------------------
class ScrollArgs(BaseModel):
    direction: Literal["up", "down", "top", "bottom"] = "down"
    amount: int = Field(600, ge=1, le=5000, description="pixels (browser) / wheel clicks (desktop)")
    mode: Mode = "browser"


@tool("scroll", "Scroll the page or window", ScrollArgs, "browser")
async def scroll(ctx: ToolContext, a: ScrollArgs) -> ToolResult:
    if a.mode == "desktop":
        await ctx.computer.scroll(a.amount // 100 * (1 if a.direction in ("up", "top") else -1) or 1)
        return ToolResult.ok("scroll", a.direction, f"Scrolled {a.direction}")
    return await ctx.browser.scroll(a.direction, a.amount)


# ---- take_screenshot ---------------------------------------------------------------------------------------------------------
class ScreenshotArgs(BaseModel):
    mode: Mode = "browser"
    analyze: bool = Field(False, description="also describe the UI with the vision model")


@tool("take_screenshot", "Capture the browser page or the whole desktop; optionally analyse it with vision", ScreenshotArgs, "browser")
async def take_screenshot(ctx: ToolContext, a: ScreenshotArgs) -> ToolResult:
    path = ctx.screenshot_path(a.mode)
    if a.mode == "desktop":
        await ctx.computer.screenshot(path)
    else:
        await ctx.browser.screenshot(path)
    data: dict = {"screenshot": str(path)}
    if a.analyze and ctx.analyzer and ctx.analyzer.available:
        data["analysis"] = (await ctx.analyzer.analyze(path)).model_dump()
    return ToolResult.ok("take_screenshot", a.mode, f"Captured {a.mode} screenshot", **data)


# ---- wait ------------------------------------------------------------------------------------------------------------------------
class WaitArgs(BaseModel):
    seconds: float = Field(2, ge=0.1, le=30)
    for_text: str = Field("", description="browser: wait until this text appears")
    for_selector: str = ""


@tool("wait", "Wait for time to pass or for text/selector to appear", WaitArgs, "browser")
async def wait(ctx: ToolContext, a: WaitArgs) -> ToolResult:
    return await ctx.browser.wait(a.seconds, a.for_text, a.for_selector)


# ---- find_element ----------------------------------------------------------------------------------------------------------------------
class FindElementArgs(BaseModel):
    description: str
    mode: Mode = "browser"


@tool("find_element", "Locate an element; returns ranked candidates (browser) or coordinates (desktop, via vision)", FindElementArgs, "browser")
async def find_element(ctx: ToolContext, a: FindElementArgs) -> ToolResult:
    if a.mode == "desktop":
        pos = await _desktop_locate(ctx, a.description)
        if pos is None:
            return ToolResult.fail("find_element", f"'{a.description}' not visible on screen", "element_not_found", target=a.description)
        return ToolResult.ok("find_element", a.description, f"Found at {pos}", x=pos[0], y=pos[1])
    return await ctx.browser.find_element(a.description)


# ---- read_page ---------------------------------------------------------------------------------------------------------------------------
class ReadPageArgs(BaseModel):
    max_chars: int = Field(6000, ge=500, le=30000)


@tool("read_page", "Read the visible text, headings and links of the current page", ReadPageArgs, "browser")
async def read_page(ctx: ToolContext, a: ReadPageArgs) -> ToolResult:
    return await ctx.browser.read_page(a.max_chars)


# ---- download_file / upload_file ------------------------------------------------------------------------------------------------------------
class DownloadArgs(BaseModel):
    url: str = Field("", description="direct file URL (preferred, e.g. raw.githubusercontent.com/...)")
    target: str = Field("", description="or: description of the download link/button to click")
    filename: str = ""
    save_dir: str = Field("", description="folder, default Downloads/NOVA")


@tool("download_file", "Download a file by URL or by clicking a download link", DownloadArgs, "browser")
async def download_file(ctx: ToolContext, a: DownloadArgs) -> ToolResult:
    if not (a.url or a.target):
        return ToolResult.fail("download_file", "Provide url or target", "invalid_args")
    save_dir = str(resolve_path(a.save_dir)) if a.save_dir else ""
    return await ctx.browser.download(a.url, a.target, "", save_dir, a.filename)


class UploadArgs(BaseModel):
    path: str
    target: str = Field("", description="description of the upload field")
    selector: str = ""


@tool("upload_file", "Attach a local file to a file input on the page (needs approval)", UploadArgs, "browser")
async def upload_file(ctx: ToolContext, a: UploadArgs) -> ToolResult:
    p = resolve_path(a.path)
    if not p.is_file():
        return ToolResult.fail("upload_file", f"{p} does not exist", "not_found", target=str(p))
    return await ctx.browser.upload(p, a.target, a.selector)
