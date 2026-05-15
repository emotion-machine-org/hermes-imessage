"""Async WebSocket client with reconnect + request/response correlation.

Python port of ``claw-messenger/plugin/src/ws/client.ts``. Single persistent
connection to the Claw Messenger relay; survives across server restarts and
network blips.

Connection lifecycle:
    await client.start()              # spawns connect loop in the background
    await client.connected_event.wait()  # wait for first successful connect
    await client.send({...})          # fire-and-forget
    resp = await client.request({...})  # awaits a correlated response
    await client.stop()               # graceful shutdown, no reconnect

Reconnect policy:
    * Exponential backoff (1s → 30s cap, with full jitter)
    * Max 50 attempts before giving up
    * Close codes 1008 (evicted) and 4003 (subscription inactive) → no reconnect
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import websockets
from websockets import State
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

MAX_RECONNECT_DELAY_S = 30.0
MAX_RECONNECT_ATTEMPTS = 50
MAX_DIAGNOSTIC_ENTRIES = 50
PING_INTERVAL_S = 30.0
REQUEST_TIMEOUT_S = 30.0
PONG_TIMEOUT_S = 10.0
NO_RECONNECT_CODES = frozenset({1008, 4003})

OnMessage = Callable[[Dict[str, Any]], Awaitable[None]]
OnEvent = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# WsClient
# ---------------------------------------------------------------------------


class WsClient:
    """Async, reconnecting WebSocket client for the Claw Messenger relay."""

    def __init__(
        self,
        server_url: str,
        api_key: str,
        on_message: OnMessage,
        on_connect: Optional[OnEvent] = None,
        on_disconnect: Optional[OnEvent] = None,
        oneshot: bool = False,
    ) -> None:
        self.server_url = server_url
        self.api_key = api_key
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._oneshot = oneshot

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stopped = False
        self._reconnect_attempt = 0
        self._correlation_counter = 0
        self._pending: Dict[str, asyncio.Future] = {}
        self._main_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._pong_deadline: Optional[float] = None

        # Public — adapter awaits this for first-connect handshake
        self.connected_event = asyncio.Event()

        # Ring-buffered diagnostics for the future diagnose tool
        self._diag_log: Deque[Dict[str, Any]] = deque(maxlen=MAX_DIAGNOSTIC_ENTRIES)
        self._error_log: Deque[Dict[str, Any]] = deque(maxlen=MAX_DIAGNOSTIC_ENTRIES)
        # Last close info — adapter inspects after disconnect to choose fatal vs retry
        self.last_close_code: Optional[int] = None
        self.last_close_reason: str = ""

    # ----- public API -----

    @property
    def connected(self) -> bool:
        ws = self._ws
        return ws is not None and ws.state is State.OPEN

    async def start(self) -> None:
        """Spawn the connect loop (does not block)."""
        if self._main_task is not None and not self._main_task.done():
            return
        self._stopped = False
        self._main_task = asyncio.create_task(self._connect_loop(), name="ws_client.connect_loop")

    async def stop(self) -> None:
        """Graceful shutdown — no reconnect, all pending requests rejected."""
        self._stopped = True
        await self._cancel_ping_task()
        if self._ws is not None:
            try:
                await self._ws.close(code=1000, reason="Client stopping")
            except Exception:
                pass
            self._ws = None
        if self._main_task is not None:
            self._main_task.cancel()
            try:
                await self._main_task
            except (asyncio.CancelledError, Exception):
                pass
            self._main_task = None
        self._reject_pending("Client stopped")
        self.connected_event.clear()

    async def send(self, message: Dict[str, Any]) -> None:
        """Fire-and-forget send. Raises if not connected."""
        ws = self._ws
        if ws is None or ws.state is not State.OPEN:
            raise RuntimeError("WebSocket not connected")
        await ws.send(json.dumps(message))

    async def request(
        self,
        message: Dict[str, Any],
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> Dict[str, Any]:
        """Send a message and await a correlated response (matched by ``id``)."""
        corr_id = str(message.get("id") or self._next_id())
        message["id"] = corr_id
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[corr_id] = fut
        try:
            await self.send(message)
        except Exception:
            self._pending.pop(corr_id, None)
            raise
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(corr_id, None)
            raise TimeoutError(f"Request {corr_id} timed out after {timeout}s")
        except Exception:
            self._pending.pop(corr_id, None)
            raise

    def get_diagnostics(self) -> Dict[str, Any]:
        return {"errors": list(self._error_log), "connection_log": list(self._diag_log)}

    # ----- internal -----

    def _next_id(self) -> str:
        self._correlation_counter += 1
        return f"corr-{self._correlation_counter}-{int(time.time() * 1000)}"

    def _build_url(self) -> str:
        parsed = urlparse(self.server_url)
        scheme = parsed.scheme.lower()
        if scheme == "https":
            scheme = "wss"
        elif scheme == "http":
            scheme = "ws"
        elif scheme in ("", "ws", "wss"):
            pass
        else:
            raise ValueError(f"Unsupported scheme {parsed.scheme!r}")
        if not scheme:
            scheme = "wss"

        path = parsed.path or ""
        if not path.endswith("/ws"):
            path = path.rstrip("/") + "/ws"

        existing = parsed.query
        suffix = urlencode({"key": self.api_key})
        query = f"{existing}&{suffix}" if existing else suffix

        return urlunparse((scheme, parsed.netloc, path, parsed.params, query, parsed.fragment))

    async def _connect_loop(self) -> None:
        """Main loop — reconnect with exponential backoff until ``stop()``."""
        try:
            while not self._stopped:
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # connect failure
                    self._log_diag("error", detail=f"connect failed: {exc!r}")
                    logger.warning("WS connect failed: %s", exc)

                if self._stopped:
                    return
                if self._oneshot:
                    return
                if self._reconnect_attempt >= MAX_RECONNECT_ATTEMPTS:
                    logger.error("WS: max reconnect attempts reached — giving up")
                    self._stopped = True
                    return
                if self.last_close_code in NO_RECONNECT_CODES:
                    logger.warning(
                        "WS: not reconnecting (code=%s reason=%s)",
                        self.last_close_code,
                        self.last_close_reason,
                    )
                    self._log_diag(
                        "evicted",
                        code=self.last_close_code,
                        reason=self.last_close_reason,
                    )
                    self._stopped = True
                    return

                delay = self._next_delay()
                logger.info(
                    "WS reconnecting in %.1fs (attempt %d)",
                    delay,
                    self._reconnect_attempt + 1,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                self._reconnect_attempt += 1
        except asyncio.CancelledError:
            return

    def _next_delay(self) -> float:
        base = min(MAX_RECONNECT_DELAY_S, 1.0 * (2 ** self._reconnect_attempt))
        return random.uniform(0.5, 1.0) * base  # full jitter (lower bound = base/2)

    async def _connect_once(self) -> None:
        url = self._build_url()
        logger.info("WS connecting...")
        try:
            ws = await websockets.connect(
                url,
                open_timeout=15,
                ping_interval=None,  # we manage our own app-level ping
                ping_timeout=None,
                close_timeout=5,
                max_size=8 * 1024 * 1024,
            )
        except InvalidStatus as exc:
            # HTTP-level rejection (401/403/etc). Surface code for fatal handling.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            self.last_close_code = status or 0
            self.last_close_reason = str(exc)
            self._log_diag("error", code=status, reason=str(exc))
            if status in (401, 403):
                # Fatal auth failure — emulate close code 4003
                self.last_close_code = 4003
            raise

        self._ws = ws
        self.last_close_code = None
        self.last_close_reason = ""
        self._reject_pending("Connection reset")
        self.connected_event.set()
        self._log_diag("connect")
        if self._on_connect is not None:
            try:
                await self._on_connect()
            except Exception:
                logger.exception("on_connect callback failed")
        self._start_ping_task()

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    logger.warning("WS dropped unparseable frame: %r", raw[:200])
                    continue
                self._handle_frame(data)
        except ConnectionClosed as exc:
            close = exc.rcvd or exc.sent
            code = getattr(close, "code", None)
            reason = getattr(close, "reason", "") or ""
            self.last_close_code = code
            self.last_close_reason = reason
            logger.info("WS disconnected (code=%s reason=%s)", code, reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("WS receive loop crashed: %s", exc)
            self._log_diag("error", detail=repr(exc))
        finally:
            await self._cancel_ping_task()
            self._ws = None
            self.connected_event.clear()
            if self.last_close_code not in (1000, None):
                self._log_diag("disconnect", code=self.last_close_code, reason=self.last_close_reason)
            if self._on_disconnect is not None:
                try:
                    await self._on_disconnect()
                except Exception:
                    logger.exception("on_disconnect callback failed")

    def _handle_frame(self, data: Dict[str, Any]) -> None:
        # Any inbound frame means the connection is healthy
        self._reconnect_attempt = 0

        # Pong response to our ping
        msg_type = data.get("type")
        if msg_type == "pong":
            self._pong_deadline = None
            return
        # Server-initiated ping → reply with pong
        if msg_type == "ping":
            asyncio.create_task(self._safe_send({"type": "pong"}))
            return

        # Correlated request/response
        corr_id = data.get("id")
        if corr_id and corr_id in self._pending:
            fut = self._pending.pop(corr_id)
            if not fut.done():
                fut.set_result(data)
            return

        # Otherwise dispatch to user handler
        if self._on_message is not None:
            asyncio.create_task(self._dispatch_message(data))

    async def _dispatch_message(self, data: Dict[str, Any]) -> None:
        try:
            await self._on_message(data)
        except Exception:
            logger.exception("on_message handler raised on data=%r", data)

    async def _safe_send(self, message: Dict[str, Any]) -> None:
        try:
            await self.send(message)
        except Exception:
            self._log_diag("send_fail", detail=str(message.get("type")))

    def _start_ping_task(self) -> None:
        if self._ping_task is not None and not self._ping_task.done():
            self._ping_task.cancel()
        self._pong_deadline = None
        self._ping_task = asyncio.create_task(self._ping_loop(), name="ws_client.ping_loop")

    async def _cancel_ping_task(self) -> None:
        task = self._ping_task
        self._ping_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL_S)
                if self._ws is None or self._ws.state is not State.OPEN:
                    return
                try:
                    await self.send({"type": "ping"})
                except Exception:
                    self._log_diag("send_fail", detail="ping")
                    continue
                self._pong_deadline = time.monotonic() + PONG_TIMEOUT_S

                # Hard-wait the timeout window; if pong arrives, handler clears
                # the deadline. Otherwise force-close to trigger reconnect.
                await asyncio.sleep(PONG_TIMEOUT_S + 0.1)
                if self._pong_deadline is not None and self._ws is not None:
                    logger.warning("WS pong timeout — forcing reconnect")
                    self._log_diag("pong_timeout")
                    try:
                        await self._ws.close(code=4000, reason="Pong timeout")
                    except Exception:
                        pass
                    return
        except asyncio.CancelledError:
            return

    def _reject_pending(self, reason: str) -> None:
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(reason))

    def _log_diag(
        self,
        kind: str,
        *,
        code: Optional[int] = None,
        reason: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": kind,
        }
        if code is not None:
            entry["code"] = code
        if reason:
            entry["reason"] = reason
        if detail:
            entry["detail"] = detail
        self._diag_log.append(entry)
        if kind in ("error", "evicted", "send_fail", "pong_timeout"):
            self._error_log.append(entry)
