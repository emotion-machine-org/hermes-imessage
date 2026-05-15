"""Out-of-process cron sender — stub, real impl lands later."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


async def standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Open a one-shot WS connection, send a message, close.

    Used by ``tools/send_message_tool`` when the gateway runner is not in this
    process (e.g. ``hermes cron`` running separately).
    """
    return {"error": "standalone_send not yet implemented"}
