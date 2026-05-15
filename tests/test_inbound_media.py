"""Inbound media handling tests.

The Claw Messenger relay pushes attachments as ``{url, mimeType}`` objects.
The adapter downloads them and caches via Hermes' built-in helpers so they
become local file paths the agent's vision tools can read.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List
from unittest.mock import AsyncMock

import pytest


# A tiny but valid PNG (1×1 red pixel) so cache_image_from_bytes accepts it
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "89000000017352474200aece1ce90000000d49444154789c63faff9f1100000000"
    "0049454e44ae426082"
)


@pytest.mark.asyncio
async def test_inbound_image_is_downloaded_and_cached(fake_server, make_adapter,
                                                     monkeypatch):
    """An inbound message with an image attachment should produce a
    MessageEvent whose ``media_urls`` points at a real cached path."""
    from hermes_claw_messenger import adapter as adapter_mod

    captured: List[Any] = []

    async def handler(ws, srv):
        await ws.send(json.dumps({
            "type": "message", "from": "+15551234567",
            "text": "look", "messageId": "m1",
            "isGroup": False,
            "attachments": [{
                "url": "https://example.test/img.png",
                "mimeType": "image/png",
            }],
        }))
        async for _ in ws:
            pass

    # Mock httpx to return the PNG bytes
    class _FakeResp:
        def __init__(self):
            self.content = _PNG_BYTES
            self.headers = {"content-type": "image/png"}
        def raise_for_status(self): return None

    class _FakeHttp:
        async def get(self, _url):
            return _FakeResp()
        async def aclose(self): return None

    async with fake_server(handler=handler) as srv:
        adapter = make_adapter(server_url=srv.url)
        async def fake_handle(event):
            captured.append(event)
        adapter.handle_message = fake_handle  # type: ignore[assignment]

        # Force the adapter to use our fake httpx client
        monkeypatch.setattr(adapter_mod, "httpx", type("X", (), {
            "AsyncClient": lambda *a, **kw: _FakeHttp(),
        }))

        assert await adapter.connect() is True
        await asyncio.sleep(0.5)
        await adapter.disconnect()

    assert len(captured) == 1
    ev = captured[0]
    assert ev.media_urls, "media should have been downloaded"
    assert ev.media_types[0].startswith("image/")
    # The cached path should be an absolute file path
    assert ev.media_urls[0].endswith(".png")
