"""Integration tests for the adapter against a fake WS server.

Hermes' BasePlatformAdapter is imported here, so this test confirms the
adapter is a real Hermes plug-and-play platform.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import pytest

# Required for Hermes' BasePlatformAdapter constructor
os.environ.setdefault("HERMES_PII_REDACTION", "")


@pytest.fixture
def make_adapter():
    """Returns a factory that builds a configured ClawMessengerAdapter."""
    from gateway.config import PlatformConfig

    from hermes_claw_messenger.adapter import ClawMessengerAdapter

    def _make(api_key="cm_live_TEST", server_url="ws://127.0.0.1:9999",
              preferred_service="iMessage"):
        os.environ["CLAW_MESSENGER_API_KEY"] = api_key
        os.environ["CLAW_MESSENGER_SERVER_URL"] = server_url
        os.environ["CLAW_MESSENGER_PREFERRED_SERVICE"] = preferred_service
        cfg = PlatformConfig(enabled=True, extra={})
        return ClawMessengerAdapter(cfg)

    return _make


# ---------------------------------------------------------------------------
# Outbound DM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_dm_round_trip(fake_server, make_adapter):
    async with fake_server() as srv:
        adapter = make_adapter(server_url=srv.url)
        assert await adapter.connect() is True
        try:
            result = await adapter.send("+15551234567", "hello from hermes")
        finally:
            await adapter.disconnect()
        assert result.success is True
        assert result.message_id == "msg_test"
        # Verify the wire format the fake server saw
        sent = [m for m in srv.received if m.get("type") == "send"]
        assert len(sent) == 1
        assert sent[0]["to"] == "+15551234567"
        assert "chatId" not in sent[0]
        assert sent[0]["service"] == "iMessage"
        assert sent[0]["parts"] == [{"type": "text", "value": "hello from hermes"}]


@pytest.mark.asyncio
async def test_send_routes_groups_by_chatid(fake_server, make_adapter):
    async with fake_server() as srv:
        adapter = make_adapter(server_url=srv.url)
        assert await adapter.connect() is True
        try:
            result = await adapter.send("group_abc123", "hi group")
        finally:
            await adapter.disconnect()
        assert result.success is True
        sent = [m for m in srv.received if m.get("type") == "send"]
        assert sent[0]["chatId"] == "group_abc123"
        assert "to" not in sent[0]


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inbound_dm_dispatches_message_event(fake_server, make_adapter):
    """A 'message' frame from the server should be dispatched to handle_message."""
    captured: List[Any] = []

    async def handler(ws, srv):
        # Push an inbound DM — server also includes chatId (the chat-record
        # UUID) even for DMs. The adapter must NOT use that UUID as chat_id
        # for DMs; it should route replies by phone.
        await ws.send(json.dumps({
            "type": "message", "from": "+15551234567",
            "text": "hi there", "messageId": "msg_inbound_1",
            "chatId": "chat_record_uuid_999",
            "isGroup": False, "attachments": [],
        }))
        async for raw in ws:
            try: srv.received.append(json.loads(raw))
            except Exception: pass

    async with fake_server(handler=handler) as srv:
        adapter = make_adapter(server_url=srv.url)

        # Monkey-patch handle_message to capture
        async def fake_handle(event):
            captured.append(event)
        adapter.handle_message = fake_handle  # type: ignore[assignment]

        assert await adapter.connect() is True
        # Give the dispatch coroutine time to run
        await asyncio.sleep(0.3)
        await adapter.disconnect()

    assert len(captured) == 1
    ev = captured[0]
    assert ev.text == "hi there"
    assert ev.source.chat_type == "dm"
    assert ev.source.chat_id == "+15551234567"  # phone, not UUID
    assert ev.source.chat_id_alt == "chat_record_uuid_999"
    assert ev.source.user_id == "+15551234567"
    assert ev.message_id == "msg_inbound_1"


@pytest.mark.asyncio
async def test_inbound_group_routes_by_chat_id(fake_server, make_adapter):
    captured: List[Any] = []

    async def handler(ws, srv):
        await ws.send(json.dumps({
            "type": "message", "from": "+15551234567",
            "text": "hi crew", "messageId": "msg_inbound_g",
            "isGroup": True, "chatId": "group_xyz_999",
            "attachments": [],
        }))
        async for raw in ws:
            try: srv.received.append(json.loads(raw))
            except Exception: pass

    async with fake_server(handler=handler) as srv:
        adapter = make_adapter(server_url=srv.url)
        async def fake_handle(event):
            captured.append(event)
        adapter.handle_message = fake_handle  # type: ignore[assignment]
        assert await adapter.connect() is True
        await asyncio.sleep(0.3)
        await adapter.disconnect()

    assert len(captured) == 1
    ev = captured[0]
    assert ev.source.chat_type == "group"
    assert ev.source.chat_id == "group_xyz_999"
    assert ev.source.user_id == "+15551234567"


# ---------------------------------------------------------------------------
# Typing + read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_typing_indicator_only_on_dm(fake_server, make_adapter):
    async with fake_server() as srv:
        adapter = make_adapter(server_url=srv.url)
        await adapter.connect()
        await adapter.send_typing("+15551234567")
        await adapter.send_typing("group_abc")  # group → no-op
        await asyncio.sleep(0.1)
        await adapter.disconnect()

        typings = [m for m in srv.received if m.get("type") == "typing.start"]
        assert len(typings) == 1
        assert typings[0]["to"] == "+15551234567"


@pytest.mark.asyncio
async def test_read_receipt_sent_for_inbound_dm_only(fake_server, make_adapter):
    """Inbound DM should trigger a 'read' frame; inbound group should not."""
    async def handler(ws, srv):
        await ws.send(json.dumps({
            "type": "message", "from": "+15551234567",
            "text": "hi", "messageId": "m1", "isGroup": False, "attachments": [],
        }))
        await asyncio.sleep(0.3)
        await ws.send(json.dumps({
            "type": "message", "from": "+15559999999",
            "text": "yo", "messageId": "m2", "isGroup": True,
            "chatId": "group_q", "attachments": [],
        }))
        async for raw in ws:
            try: srv.received.append(json.loads(raw))
            except Exception: pass

    async with fake_server(handler=handler) as srv:
        adapter = make_adapter(server_url=srv.url)
        async def noop(ev): pass
        adapter.handle_message = noop  # type: ignore[assignment]
        await adapter.connect()
        await asyncio.sleep(1.0)
        await adapter.disconnect()
        reads = [m for m in srv.received if m.get("type") == "read"]
        assert len(reads) == 1
        assert reads[0]["to"] == "+15551234567"


# ---------------------------------------------------------------------------
# create_group helper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_group_payload(fake_server, make_adapter):
    async def handler(ws, srv):
        async for raw in ws:
            msg = json.loads(raw)
            srv.received.append(msg)
            if msg.get("type") == "send" and isinstance(msg.get("to"), list):
                await ws.send(json.dumps({
                    "id": msg["id"], "ok": True,
                    "messageId": "msg_grp_1", "chatId": "group_new_42",
                }))

    async with fake_server(handler=handler) as srv:
        adapter = make_adapter(server_url=srv.url)
        await adapter.connect()
        ok, info = await adapter.create_group(
            ["+15551234567", "+14155551212"], "kickoff",
        )
        await adapter.disconnect()
        assert ok is True
        assert info["chat_id"] == "group_new_42"
        assert info["message_id"] == "msg_grp_1"
        sent = [m for m in srv.received if m.get("type") == "send"]
        assert sent[0]["to"] == ["+15551234567", "+14155551212"]
