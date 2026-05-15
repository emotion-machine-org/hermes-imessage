"""Out-of-process cron sender — opens a one-shot WS connection.

Used by ``tools/send_message_tool`` when the gateway is not running in the
caller's process (e.g. ``hermes cron run`` as a separate process).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .outbound import build_send_payload
from .ws_client import WsClient

logger = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "wss://claw-messenger.onrender.com"


async def standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Connect, send a single message, close. Returns the send-tool result envelope."""
    extra = getattr(pconfig, "extra", None) or {}
    api_key = os.getenv("CLAW_MESSENGER_API_KEY") or str(extra.get("api_key") or "")
    if not api_key:
        return {"error": "CLAW_MESSENGER_API_KEY is not set"}
    server_url = (
        os.getenv("CLAW_MESSENGER_SERVER_URL")
        or str(extra.get("server_url") or DEFAULT_SERVER_URL)
    )
    preferred_service = (
        os.getenv("CLAW_MESSENGER_PREFERRED_SERVICE")
        or str(extra.get("preferred_service") or "iMessage")
    )

    if not chat_id:
        return {"error": "chat_id is required"}
    if not message and not media_files:
        return {"error": "message or media is required"}

    # Build a payload — first media file as inline URL if it's already an URL,
    # otherwise we send text only (no upload support in v1).
    media_url = None
    if media_files:
        first = str(media_files[0])
        if first.startswith(("http://", "https://")):
            media_url = first
        else:
            logger.warning(
                "claw_messenger standalone_send: local media file %s ignored "
                "(v1 supports remote URLs only)", first,
            )

    try:
        payload = build_send_payload(
            chat_id=chat_id, text=message,
            preferred_service=preferred_service, media_url=media_url,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    client = WsClient(
        server_url=server_url, api_key=api_key,
        on_message=_noop, oneshot=True,
    )
    await client.start()
    try:
        import asyncio
        try:
            await asyncio.wait_for(client.connected_event.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            return {"error": "connect timed out"}
        try:
            resp = await client.request(payload, timeout=30.0)
        except Exception as exc:
            return {"error": f"send failed: {exc!r}"}
        if resp.get("ok"):
            return {"success": True, "message_id": str(resp.get("messageId") or "")}
        return {"error": str(resp.get("error") or "send rejected")}
    finally:
        await client.stop()


async def _noop(_data):
    return None
