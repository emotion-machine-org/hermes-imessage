"""Tests for the claw_messenger_create_group agent tool."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Tuple

import pytest

from hermes_claw_messenger import tools as cm_tools


class _FakeAdapter:
    def __init__(self, *, ok: bool, info: Dict[str, Any]):
        self.ok = ok
        self.info = info
        self.calls: List[Tuple[List[str], str]] = []

    async def create_group(self, phones, first_message):
        self.calls.append((list(phones), first_message))
        return self.ok, self.info


@pytest.fixture
def patch_runner(monkeypatch):
    """Patch _get_running_adapter to return a fake."""
    holder: Dict[str, Any] = {}

    def install(adapter):
        holder["adapter"] = adapter

    def _get():
        return holder.get("adapter")

    monkeypatch.setattr(cm_tools, "_get_running_adapter", _get)
    return install


@pytest.mark.asyncio
async def test_create_group_success(patch_runner):
    fake = _FakeAdapter(ok=True, info={"chat_id": "group_X", "message_id": "m1"})
    patch_runner(fake)
    out = await cm_tools.create_group_handler({
        "phone_numbers": ["+12125550100", "+14155550199"],
        "first_message": "hi crew",
    })
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["chat_id"] == "group_X"
    assert fake.calls == [(["+12125550100", "+14155550199"], "hi crew")]


@pytest.mark.asyncio
async def test_create_group_accepts_json_string(patch_runner):
    fake = _FakeAdapter(ok=True, info={"chat_id": "group_Y", "message_id": "m2"})
    patch_runner(fake)
    out = await cm_tools.create_group_handler(json.dumps({
        "phone_numbers": ["+12125550100", "+14155550199"],
        "first_message": "hello",
    }))
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["chat_id"] == "group_Y"


@pytest.mark.asyncio
async def test_create_group_rejects_single_phone(patch_runner):
    patch_runner(_FakeAdapter(ok=True, info={}))
    out = await cm_tools.create_group_handler({
        "phone_numbers": ["+12125550100"],
        "first_message": "solo",
    })
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "at least 2" in payload["error"]


@pytest.mark.asyncio
async def test_create_group_no_running_adapter(patch_runner):
    patch_runner(None)
    out = await cm_tools.create_group_handler({
        "phone_numbers": ["+12125550100", "+14155550199"],
        "first_message": "hi",
    })
    payload = json.loads(out)
    assert payload["ok"] is False
    assert "not running" in payload["error"]
