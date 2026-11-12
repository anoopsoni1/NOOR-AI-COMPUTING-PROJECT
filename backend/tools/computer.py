"""Safe PyAutoGUI abstraction. The LLM never touches PyAutoGUI; only the tool layer calls this class,
and only after the permission engine has approved the action."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any


class ComputerHalted(RuntimeError):
    pass


ALLOWED_KEYS = None  # validated lazily against pyautogui.KEYBOARD_KEYS
_BLOCKED_HOTKEYS = {("alt", "f4"), ("ctrl", "alt", "delete"), ("win", "l"), ("ctrl", "shift", "esc")}


class Computer:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._halt = threading.Event()
        self._pag: Any = None

    # ---- lifecycle ------------------------------------------------------------
    def _lib(self) -> Any:
        if not self.enabled:
            raise RuntimeError("Desktop automation is disabled (DESKTOP_AUTOMATION_ENABLED=false)")
        if self._halt.is_set():
            raise ComputerHalted("Desktop automation halted by emergency stop")
        if self._pag is None:
            import pyautogui  # imported lazily: needs a display

            pyautogui.FAILSAFE = True  # moving the mouse to a screen corner aborts
            pyautogui.PAUSE = 0.15
            self._pag = pyautogui
        return self._pag

    def halt(self) -> None:
        self._halt.set()

    def resume(self) -> None:
        self._halt.clear()

    # ---- primitives (sync, executed in a worker thread) --------------------------------
    def size(self) -> tuple[int, int]:
        s = self._lib().size()
        return int(s.width), int(s.height)

    def _click(self, x: int, y: int, button: str, clicks: int) -> None:
        pag = self._lib()
        w, h = self.size()
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"coordinates ({x},{y}) outside screen {w}x{h}")
        pag.click(x, y, clicks=clicks, button=button, duration=0.15)

    def _type(self, text: str) -> None:
        pag = self._lib()
        if text.isascii():
            pag.write(text, interval=0.02)
        else:  # non-ASCII: paste through the clipboard
            import pyperclip

            pyperclip.copy(text)
            pag.hotkey("ctrl", "v")

    def _press(self, key: str) -> None:
        pag = self._lib()
        if key.lower() not in pag.KEYBOARD_KEYS:
            raise ValueError(f"unknown key '{key}'")
        pag.press(key.lower())

    def _hotkey(self, keys: tuple[str, ...]) -> None:
        pag = self._lib()
        norm = tuple(k.lower() for k in keys)
        if norm in _BLOCKED_HOTKEYS:
            raise PermissionError(f"hotkey {'+'.join(norm)} is not allowed")
        for k in norm:
            if k not in pag.KEYBOARD_KEYS:
                raise ValueError(f"unknown key '{k}'")
        pag.hotkey(*norm)

    def _scroll(self, amount: int) -> None:
        self._lib().scroll(amount)

    def _screenshot(self, path: Path) -> tuple[int, int]:
        img = self._lib().screenshot()
        img.save(path)
        return img.width, img.height

    # ---- async facade ---------------------------------------------------------------
    async def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        await asyncio.to_thread(self._click, x, y, button, clicks)

    async def type(self, text: str) -> None:
        await asyncio.to_thread(self._type, text)

    async def press(self, key: str) -> None:
        await asyncio.to_thread(self._press, key)

    async def hotkey(self, *keys: str) -> None:
        await asyncio.to_thread(self._hotkey, keys)

    async def scroll(self, amount: int) -> None:
        await asyncio.to_thread(self._scroll, amount)

    async def screenshot(self, path: Path) -> tuple[int, int]:
        return await asyncio.to_thread(self._screenshot, path)
