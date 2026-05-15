"""Tests for the out-of-process standalone sender (used by cron)."""

from __future__ import annotations

import json
import os

import pytest


@pytest.mark.asyncio
async def test_standalone_send_round_trip(fake_server, monkeypatch):
    from gateway.config import PlatformConfig

    from hermes_claw_messenger.standalone import standalone_send

    async with fake_server() as srv:
        monkeypatch.setenv("CLAW_MESSENGER_API_KEY", "cm_live_TEST")
        monkeypatch.setenv("CLAW_MESSENGER_SERVER_URL", srv.url)
        monkeypatch.setenv("CLAW_MESSENGER_PREFERRED_SERVICE", "iMessage")

        result = await standalone_send(
            PlatformConfig(enabled=True, extra={}),
            "+15551234567",
            "hello from cron",
        )

    assert result.get("success") is True
    assert result.get("message_id") == "msg_test"
    sent = [m for m in srv.received if m.get("type") == "send"]
    assert sent[0]["to"] == "+15551234567"
    assert sent[0]["service"] == "iMessage"


@pytest.mark.asyncio
async def test_standalone_send_missing_key(monkeypatch):
    from gateway.config import PlatformConfig
    from hermes_claw_messenger.standalone import standalone_send

    monkeypatch.delenv("CLAW_MESSENGER_API_KEY", raising=False)
    result = await standalone_send(
        PlatformConfig(enabled=True, extra={}),
        "+15551234567",
        "hi",
    )
    assert "error" in result
    assert "API_KEY" in result["error"]


@pytest.mark.asyncio
async def test_standalone_send_routes_group(fake_server, monkeypatch):
    from gateway.config import PlatformConfig
    from hermes_claw_messenger.standalone import standalone_send

    async with fake_server() as srv:
        monkeypatch.setenv("CLAW_MESSENGER_API_KEY", "cm_live_TEST")
        monkeypatch.setenv("CLAW_MESSENGER_SERVER_URL", srv.url)
        result = await standalone_send(
            PlatformConfig(enabled=True, extra={}),
            "group_xyz_123",
            "hi group",
        )
    assert result.get("success") is True
    sent = [m for m in srv.received if m.get("type") == "send"]
    assert sent[0]["chatId"] == "group_xyz_123"
    assert "to" not in sent[0]
