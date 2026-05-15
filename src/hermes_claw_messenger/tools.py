"""Agent tools exposed by the plugin."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


CREATE_GROUP_SCHEMA: Dict[str, Any] = {
    "name": "claw_messenger_create_group",
    "description": (
        "Create a new iMessage / RCS / SMS group chat with two or more phone "
        "numbers and send the first message. Returns the chat_id you must use "
        "to send future messages to this group."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": (
                    "E.164 phone numbers of the other participants, "
                    "e.g. ['+12125550100', '+14155550199']."
                ),
            },
            "first_message": {
                "type": "string",
                "description": "The first message to send to the new group.",
            },
        },
        "required": ["phone_numbers", "first_message"],
    },
}


async def create_group_handler(arguments: Any, *_args, **_kwargs) -> str:
    """Look up the running adapter and call its ``create_group`` helper.

    Returns a JSON-encoded string so it can be embedded in the agent's tool
    transcript regardless of caller envelope.
    """
    args = _coerce_args(arguments)
    phones = args.get("phone_numbers") or []
    first_message = str(args.get("first_message") or "").strip()
    if not isinstance(phones, list) or len(phones) < 2:
        return json.dumps({"ok": False,
                           "error": "phone_numbers must be a list of at least 2 E.164 numbers"})
    if not first_message:
        return json.dumps({"ok": False, "error": "first_message is required"})

    adapter = _get_running_adapter()
    if adapter is None:
        return json.dumps({"ok": False,
                           "error": "claw_messenger adapter is not running"})

    ok, info = await adapter.create_group(phones, first_message)
    if ok:
        return json.dumps({"ok": True, **info})
    return json.dumps({"ok": False, **info})


def _coerce_args(arguments: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments) or {}
        except Exception:
            return {}
    return {}


def _get_running_adapter():
    """Return the live ClawMessengerAdapter or None."""
    try:
        from gateway.run import _gateway_runner_ref
    except Exception:
        return None
    try:
        runner = _gateway_runner_ref()
    except Exception:
        return None
    if runner is None:
        return None
    try:
        from gateway.config import Platform
        platform = Platform("claw_messenger")
    except Exception:
        return None
    return runner.adapters.get(platform)
