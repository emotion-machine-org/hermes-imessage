"""Outbound message helpers — build the wire format the relay expects."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# E.164: optional leading +, 10–15 digits.
_E164 = re.compile(r"^\+?\d{10,15}$")


def looks_like_e164(target: str) -> bool:
    if not target:
        return False
    return bool(_E164.match(target.strip()))


def normalize_e164(raw: str) -> Optional[str]:
    """Strip whitespace/separators and prefix with + if missing."""
    if not raw:
        return None
    stripped = re.sub(r"[\s\-().]", "", raw.strip())
    if re.fullmatch(r"\+\d{10,15}", stripped):
        return stripped
    if re.fullmatch(r"\d{10,15}", stripped):
        return "+" + stripped
    return None


def build_send_payload(
    chat_id: str,
    text: str,
    *,
    preferred_service: Optional[str] = None,
    media_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a {"type":"send", …} request body.

    DMs route by ``to`` (E.164 phone), groups route by ``chatId``. Body is
    a list of ``parts`` (text + optional media URL). Mirrors the wire format
    produced by ``plugin/src/outbound/send.ts``.
    """
    parts: List[Dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "value": text})
    if media_url:
        parts.append({"type": "media", "url": media_url})
    if not parts:
        raise ValueError("send needs either text or media_url")

    payload: Dict[str, Any] = {"type": "send", "parts": parts}
    if preferred_service:
        payload["service"] = preferred_service

    target = chat_id.strip() if chat_id else ""
    if looks_like_e164(target):
        payload["to"] = normalize_e164(target) or target
    else:
        payload["chatId"] = target
    return payload


def build_group_create_payload(
    phones: List[str],
    text: str,
    *,
    preferred_service: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a payload that creates a new group with 2+ recipients."""
    normalized = [normalize_e164(p) for p in phones]
    valid = [p for p in normalized if p]
    if len(valid) < 2:
        raise ValueError("group create requires at least 2 valid E.164 phone numbers")

    payload: Dict[str, Any] = {
        "type": "send",
        "to": valid,
        "parts": [{"type": "text", "value": text}],
    }
    if preferred_service:
        payload["service"] = preferred_service
    return payload
