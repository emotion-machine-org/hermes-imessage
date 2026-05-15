"""Shared pytest fixtures — a fake Claw Messenger WebSocket server."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import pytest
import websockets


@pytest.fixture
def fake_server():
    """Returns a context manager that yields a `FakeServer` running on a random port."""
    return _fake_server


@asynccontextmanager
async def _fake_server(handler: Optional[Callable[..., Awaitable[None]]] = None,
                       auto_pong: bool = True) -> AsyncIterator["FakeServer"]:
    fs = FakeServer(handler=handler, auto_pong=auto_pong)
    await fs.start()
    try:
        yield fs
    finally:
        await fs.stop()


class FakeServer:
    """A tiny WebSocket echo / scripted server for testing the client."""

    def __init__(
        self,
        handler: Optional[Callable[..., Awaitable[None]]] = None,
        *,
        auto_pong: bool = True,
    ):
        self.handler = handler
        self.auto_pong = auto_pong
        self.server: Optional[websockets.Server] = None
        self.port: int = 0
        self.received: List[Dict[str, Any]] = []
        self.connections: List[websockets.WebSocketServerProtocol] = []
        self.connect_count = 0
        self.last_query: Dict[str, list] = {}

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def start(self) -> None:
        async def _wrap(ws):
            # In websockets>=14 the protocol no longer carries a separate path
            # arg — query string lives on ``ws.request.path``.
            req = getattr(ws, "request", None)
            raw_path = getattr(req, "path", "") if req else ""
            self.last_query = parse_qs(urlparse(raw_path).query)
            self.connect_count += 1
            self.connections.append(ws)
            try:
                if self.handler is not None:
                    await self.handler(ws, self)
                else:
                    await self._default_handler(ws)
            finally:
                if ws in self.connections:
                    self.connections.remove(ws)

        self.server = await websockets.serve(_wrap, "127.0.0.1", 0)
        # Pick the actual bound port
        sockets = list(self.server.sockets or [])
        if not sockets:
            raise RuntimeError("Fake server failed to bind")
        self.port = sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _default_handler(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            self.received.append(msg)
            if msg.get("type") == "ping" and self.auto_pong:
                await ws.send(json.dumps({"type": "pong"}))
            elif msg.get("type") == "send" and msg.get("id"):
                await ws.send(json.dumps({
                    "id": msg["id"],
                    "ok": True,
                    "messageId": "msg_test",
                    "chatId": msg.get("to") or msg.get("chatId") or "",
                }))

    async def push(self, data: Dict[str, Any]) -> None:
        """Push a server-initiated frame to all live connections."""
        for ws in list(self.connections):
            try:
                await ws.send(json.dumps(data))
            except Exception:
                pass

    async def close_all(self, code: int = 1000, reason: str = "") -> None:
        for ws in list(self.connections):
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass
