"""Tests for the async WebSocket client."""

from __future__ import annotations

import asyncio
import json

import pytest

from hermes_claw_messenger.ws_client import WsClient


async def _wait_connected(client: WsClient, timeout: float = 5) -> None:
    await asyncio.wait_for(client.connected_event.wait(), timeout=timeout)


@pytest.mark.asyncio
async def test_connects_and_sends_api_key_in_query(fake_server):
    async with fake_server() as srv:
        received = []
        client = WsClient(
            server_url=srv.url, api_key="cm_live_TESTKEY",
            on_message=lambda data: received.append(data) or asyncio.sleep(0),
        )
        await client.start()
        await _wait_connected(client)
        assert client.connected is True
        # API key is passed in the WS URL query
        assert srv.last_query.get("key") == ["cm_live_TESTKEY"]
        await client.stop()


@pytest.mark.asyncio
async def test_request_response_correlation(fake_server):
    async with fake_server() as srv:
        async def on_message(data):
            return None

        client = WsClient(server_url=srv.url, api_key="k", on_message=on_message)
        await client.start()
        await _wait_connected(client)

        resp = await client.request({"type": "send", "to": "+12125550100",
                                     "parts": [{"type": "text", "value": "hi"}]})
        assert resp["ok"] is True
        assert resp["messageId"] == "msg_test"
        # Server must have echoed our id
        assert resp["id"] == srv.received[-1]["id"]
        await client.stop()


@pytest.mark.asyncio
async def test_pong_to_server_ping(fake_server):
    """Server-initiated ping must trigger a pong reply."""
    async def handler(ws, srv):
        await ws.send(json.dumps({"type": "ping"}))
        # Expect the client to pong back
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=2)
            srv.received.append(json.loads(raw))
        except asyncio.TimeoutError:
            pass

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k",
                          on_message=lambda d: asyncio.sleep(0))
        await client.start()
        await _wait_connected(client)
        # Wait long enough for the server to recv the pong
        await asyncio.sleep(0.5)
        assert any(m.get("type") == "pong" for m in srv.received), srv.received
        await client.stop()


@pytest.mark.asyncio
async def test_no_reconnect_on_subscription_inactive(fake_server):
    """Close code 4003 must mark the client stopped — no further reconnect."""
    async def handler(ws, srv):
        await ws.close(code=4003, reason="Subscription inactive")

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k",
                          on_message=lambda d: asyncio.sleep(0))
        await client.start()
        # Wait for one connection attempt and close to play out
        await asyncio.sleep(0.5)
        assert srv.connect_count == 1
        # Give the connect loop time to (not) reconnect
        await asyncio.sleep(1.5)
        assert srv.connect_count == 1, "client should not have reconnected"
        assert client.last_close_code == 4003
        await client.stop()


@pytest.mark.asyncio
async def test_reconnect_on_unexpected_close(fake_server):
    """Server-initiated close with a non-fatal code triggers reconnect."""
    closed_once = {"done": False}

    async def handler(ws, srv):
        if not closed_once["done"]:
            closed_once["done"] = True
            await ws.close(code=1011, reason="transient")
            return
        # Second connection — stay open
        async for _ in ws:
            pass

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k",
                          on_message=lambda d: asyncio.sleep(0))
        await client.start()
        # Wait for the first connect+close, then for the reconnect
        deadline = asyncio.get_running_loop().time() + 5.0
        while srv.connect_count < 2 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
        assert srv.connect_count >= 2, "expected at least one reconnect"
        await client.stop()


@pytest.mark.asyncio
async def test_request_timeout(fake_server):
    """Requests time out cleanly if the server never responds."""
    async def handler(ws, srv):
        # Consume forever without responding
        async for _ in ws:
            pass

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k",
                          on_message=lambda d: asyncio.sleep(0))
        await client.start()
        await _wait_connected(client)
        with pytest.raises(TimeoutError):
            await client.request({"type": "send", "to": "+12125550100",
                                  "parts": [{"type": "text", "value": "x"}]}, timeout=0.5)
        await client.stop()


@pytest.mark.asyncio
async def test_inbound_message_dispatch(fake_server):
    """Server-initiated frames without an ``id`` go to on_message."""
    inbound = []

    async def on_message(data):
        inbound.append(data)

    async def handler(ws, srv):
        await ws.send(json.dumps({
            "type": "message", "from": "+12125550199", "text": "hello",
            "messageId": "abc", "isGroup": False, "attachments": [],
        }))
        async for raw in ws:
            try: srv.received.append(json.loads(raw))
            except Exception: pass

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k", on_message=on_message)
        await client.start()
        await _wait_connected(client)
        await asyncio.sleep(0.5)
        assert any(d.get("type") == "message" and d.get("from") == "+12125550199"
                   for d in inbound), inbound
        await client.stop()


@pytest.mark.asyncio
async def test_stop_rejects_pending_requests(fake_server):
    """``stop()`` must reject all in-flight requests."""
    async def handler(ws, srv):
        async for _ in ws:
            pass

    async with fake_server(handler=handler) as srv:
        client = WsClient(server_url=srv.url, api_key="k",
                          on_message=lambda d: asyncio.sleep(0))
        await client.start()
        await _wait_connected(client)
        task = asyncio.create_task(client.request({"type": "send",
                                                   "parts": [{"type": "text", "value": "x"}]},
                                                  timeout=10))
        await asyncio.sleep(0.05)
        await client.stop()
        with pytest.raises(Exception):
            await task
