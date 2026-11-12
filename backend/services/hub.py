"""WebSocket broadcast hub."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("nova.hub")


class EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._listeners: list[Any] = []

    def subscribe(self, fn: Any) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Any) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    async def connect(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                res = fn(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001
                pass
        if not self._clients:
            return
        results = await asyncio.gather(*(self._send(ws, event) for ws in list(self._clients)), return_exceptions=True)
        del results

    async def _send(self, ws: WebSocket, event: dict[str, Any]) -> None:
        try:
            await ws.send_json(event)
        except Exception:  # noqa: BLE001 - dead socket
            self._clients.discard(ws)
