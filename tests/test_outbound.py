"""Tests for outbound payload builders."""

from __future__ import annotations

import pytest

from hermes_claw_messenger.outbound import (
    build_group_create_payload,
    build_send_payload,
    looks_like_e164,
    normalize_e164,
)


@pytest.mark.parametrize("raw, expected", [
    ("+15551234567", True),
    ("15551234567", True),
    ("+442071234567", True),
    ("group_abc_123", False),
    ("", False),
    ("not-a-number", False),
])
def test_looks_like_e164(raw, expected):
    assert looks_like_e164(raw) is expected


@pytest.mark.parametrize("raw, expected", [
    ("+1 (555) 123-4567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+442071234567", "+442071234567"),
    ("garbage", None),
])
def test_normalize_e164(raw, expected):
    assert normalize_e164(raw) == expected


def test_build_send_payload_dm_text():
    payload = build_send_payload(
        chat_id="+15551234567", text="hi", preferred_service="iMessage",
    )
    assert payload["type"] == "send"
    assert payload["to"] == "+15551234567"
    assert "chatId" not in payload
    assert payload["service"] == "iMessage"
    assert payload["parts"] == [{"type": "text", "value": "hi"}]


def test_build_send_payload_dm_text_normalizes_e164():
    payload = build_send_payload(chat_id="15551234567", text="hi")
    assert payload["to"] == "+15551234567"


def test_build_send_payload_group_routes_by_chatid():
    payload = build_send_payload(chat_id="group_xyz_123", text="hello")
    assert payload["chatId"] == "group_xyz_123"
    assert "to" not in payload


def test_build_send_payload_with_media():
    payload = build_send_payload(
        chat_id="+15551234567",
        text="caption",
        media_url="https://example.com/img.png",
    )
    assert payload["parts"] == [
        {"type": "text", "value": "caption"},
        {"type": "media", "url": "https://example.com/img.png"},
    ]


def test_build_send_payload_media_only():
    payload = build_send_payload(
        chat_id="+15551234567",
        text="",
        media_url="https://example.com/img.png",
    )
    assert payload["parts"] == [{"type": "media", "url": "https://example.com/img.png"}]


def test_build_send_payload_rejects_empty():
    with pytest.raises(ValueError):
        build_send_payload(chat_id="+15551234567", text="")


def test_build_group_create_payload_normalizes():
    payload = build_group_create_payload(
        ["15551234567", "+14155551212"],
        "hi crew",
        preferred_service="iMessage",
    )
    assert payload["to"] == ["+15551234567", "+14155551212"]
    assert payload["parts"] == [{"type": "text", "value": "hi crew"}]
    assert payload["service"] == "iMessage"


def test_build_group_create_payload_requires_two():
    with pytest.raises(ValueError):
        build_group_create_payload(["+15551234567"], "solo")
