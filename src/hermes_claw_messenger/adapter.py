"""Claw Messenger platform adapter for Hermes Agent.

Bridges iMessage / RCS / SMS via the Claw Messenger relay into Hermes'
gateway. One persistent WebSocket connection per running adapter; inbound
frames become :class:`MessageEvent`s; outbound calls build request payloads
and await correlated responses.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)

from .outbound import (
    build_group_create_payload,
    build_send_payload,
    looks_like_e164,
    normalize_e164,
)
from .ws_client import NO_RECONNECT_CODES, WsClient

logger = logging.getLogger(__name__)


DEFAULT_SERVER_URL = "wss://claw-messenger.onrender.com"
DEFAULT_PREFERRED_SERVICE = "iMessage"
DEFAULT_REQUEST_TIMEOUT_S = 30.0
DEFAULT_MEDIA_DOWNLOAD_TIMEOUT_S = 30.0
MAX_INBOUND_MEDIA_SIZE = 20 * 1024 * 1024  # 20MB
MAX_MESSAGE_LENGTH = 10000

# Map MIME type prefix → MessageType
_MEDIA_KIND: Dict[str, MessageType] = {
    "image/": MessageType.PHOTO,
    "video/": MessageType.VIDEO,
    "audio/": MessageType.AUDIO,
}


def check_requirements() -> bool:
    """Verify runtime deps are importable."""
    try:
        import websockets  # noqa: F401
        import httpx as _httpx  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: PlatformConfig) -> bool:
    """Called before adapter construction. Requires an API key in the env."""
    api_key = os.getenv("CLAW_MESSENGER_API_KEY")
    extra = getattr(config, "extra", {}) or {}
    return bool(api_key or extra.get("api_key"))


class ClawMessengerAdapter(BasePlatformAdapter):
    """Adapter for the Claw Messenger relay (iMessage/RCS/SMS)."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig, **kwargs):
        platform = Platform("claw_messenger")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        self.api_key: str = os.getenv("CLAW_MESSENGER_API_KEY") or str(extra.get("api_key") or "")
        self.server_url: str = (
            os.getenv("CLAW_MESSENGER_SERVER_URL")
            or str(extra.get("server_url") or DEFAULT_SERVER_URL)
        )
        self.preferred_service: str = (
            os.getenv("CLAW_MESSENGER_PREFERRED_SERVICE")
            or str(extra.get("preferred_service") or DEFAULT_PREFERRED_SERVICE)
        )

        # Runtime state
        self._ws: Optional[WsClient] = None
        self._http: Optional[httpx.AsyncClient] = None
        # Track the last inbound timestamp so we can request a sync replay on
        # reconnect — populated whenever a "message" frame arrives.
        self._last_message_at: Optional[str] = None

    @property
    def name(self) -> str:
        return "Claw Messenger"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        if not self.api_key:
            self._set_fatal_error(
                "missing_api_key",
                "CLAW_MESSENGER_API_KEY is not set",
                retryable=False,
            )
            return False

        self._http = httpx.AsyncClient(timeout=DEFAULT_MEDIA_DOWNLOAD_TIMEOUT_S)
        self._ws = WsClient(
            server_url=self.server_url,
            api_key=self.api_key,
            on_message=self._handle_ws_frame,
            on_connect=self._on_ws_connect,
            on_disconnect=self._on_ws_disconnect,
        )
        await self._ws.start()
        try:
            await asyncio.wait_for(self._ws.connected_event.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            close_code = self._ws.last_close_code if self._ws else None
            close_reason = self._ws.last_close_reason if self._ws else ""
            if close_code in NO_RECONNECT_CODES:
                # Surface the fatal close so Hermes shows a meaningful status
                self._set_fatal_error(
                    f"ws_close_{close_code}",
                    close_reason or f"WebSocket closed with code {close_code}",
                    retryable=False,
                )
            logger.error(
                "claw_messenger: connect timed out (last_close_code=%s reason=%s)",
                close_code, close_reason,
            )
            await self.disconnect()
            return False
        return True

    async def disconnect(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.stop()
            except Exception:
                logger.exception("claw_messenger: ws.stop() raised")
            self._ws = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None

    async def _on_ws_connect(self) -> None:
        logger.info("claw_messenger: connected to %s", self.server_url)
        if self._last_message_at and self._ws is not None:
            try:
                await self._ws.send({"type": "sync", "since": self._last_message_at})
            except Exception:
                logger.debug("claw_messenger: sync request failed (non-fatal)")

    async def _on_ws_disconnect(self) -> None:
        logger.info("claw_messenger: disconnected")

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if self._ws is None:
            return SendResult(success=False, error="adapter not connected", retryable=True)
        if not content or not content.strip():
            return SendResult(success=True, message_id="", raw_response={"skipped": "empty"})

        payload = build_send_payload(
            chat_id=chat_id,
            text=content,
            preferred_service=self.preferred_service,
        )
        try:
            resp = await self._ws.request(payload, timeout=DEFAULT_REQUEST_TIMEOUT_S)
        except TimeoutError as exc:
            return SendResult(success=False, error=str(exc), retryable=True)
        except Exception as exc:
            return SendResult(success=False, error=f"send failed: {exc!r}", retryable=True)

        if resp.get("ok"):
            return SendResult(
                success=True,
                message_id=str(resp.get("messageId") or ""),
                raw_response=resp,
            )
        return SendResult(
            success=False,
            error=str(resp.get("error") or "send rejected"),
            raw_response=resp,
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if self._ws is None or not looks_like_e164(chat_id):
            return
        target = normalize_e164(chat_id) or chat_id
        try:
            await self._ws.send({"type": "typing.start", "to": target})
        except Exception:
            logger.debug("claw_messenger: typing.start send failed", exc_info=True)

    async def send_image(self, chat_id, image_url, caption=None) -> SendResult:
        return await self._send_media(chat_id, image_url, caption)

    async def send_video(self, chat_id, video_url, caption=None) -> SendResult:
        return await self._send_media(chat_id, video_url, caption)

    async def send_voice(self, chat_id, voice_url, caption=None) -> SendResult:
        return await self._send_media(chat_id, voice_url, caption)

    async def send_document(self, chat_id, document_url, caption=None) -> SendResult:
        return await self._send_media(chat_id, document_url, caption)

    async def _send_media(
        self,
        chat_id: str,
        media_url: str,
        caption: Optional[str],
    ) -> SendResult:
        if self._ws is None:
            return SendResult(success=False, error="adapter not connected", retryable=True)
        payload = build_send_payload(
            chat_id=chat_id,
            text=caption or "",
            preferred_service=self.preferred_service,
            media_url=media_url,
        )
        try:
            resp = await self._ws.request(payload, timeout=DEFAULT_REQUEST_TIMEOUT_S)
        except Exception as exc:
            return SendResult(success=False, error=f"send media failed: {exc!r}", retryable=True)
        if resp.get("ok"):
            return SendResult(success=True, message_id=str(resp.get("messageId") or ""), raw_response=resp)
        return SendResult(success=False, error=str(resp.get("error") or "send rejected"))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        if looks_like_e164(chat_id):
            return {"name": chat_id, "type": "dm", "chat_id": chat_id}
        return {"name": chat_id, "type": "group", "chat_id": chat_id}

    # ------------------------------------------------------------------
    # Group helpers (used by the create-group agent tool)
    # ------------------------------------------------------------------

    async def create_group(
        self,
        phones: List[str],
        first_message: str,
    ) -> Tuple[bool, Dict[str, Any]]:
        if self._ws is None:
            return False, {"error": "adapter not connected"}
        try:
            payload = build_group_create_payload(
                phones, first_message, preferred_service=self.preferred_service,
            )
        except ValueError as exc:
            return False, {"error": str(exc)}
        try:
            resp = await self._ws.request(payload, timeout=DEFAULT_REQUEST_TIMEOUT_S)
        except Exception as exc:
            return False, {"error": f"create_group failed: {exc!r}"}
        if resp.get("ok"):
            return True, {"chat_id": resp.get("chatId", ""), "message_id": resp.get("messageId", "")}
        return False, {"error": str(resp.get("error") or "create_group rejected")}

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def _handle_ws_frame(self, data: Dict[str, Any]) -> None:
        msg_type = data.get("type")
        if msg_type == "message":
            await self._handle_inbound_message(data)
        elif msg_type == "status":
            status = data.get("status")
            mid = data.get("messageId")
            if status == "failed":
                logger.warning("claw_messenger: message %s failed", mid)
            else:
                logger.debug("claw_messenger: message %s status=%s", mid, status)
        elif msg_type == "typing":
            # informational; ignore
            pass
        elif msg_type == "reaction":
            # v1: not surfaced to the agent
            pass
        elif msg_type == "sync.done":
            logger.info("claw_messenger: sync complete (count=%s)", data.get("count"))
        elif msg_type == "error":
            logger.error("claw_messenger: server error: %s", data.get("message"))
        else:
            logger.debug("claw_messenger: unhandled frame type=%s", msg_type)

    async def _handle_inbound_message(self, data: Dict[str, Any]) -> None:
        from_phone = str(data.get("from") or "").strip()
        text = str(data.get("text") or "")
        message_id = str(data.get("messageId") or "")
        is_group = bool(data.get("isGroup"))
        # For DMs use the sender phone as the chat_id so replies route by phone;
        # the chat-record UUID is stashed in chat_id_alt for traceability.
        # For groups the chat_id IS the chatId UUID — that's how replies route.
        chat_record_id = str(data.get("chatId") or "")
        chat_id = chat_record_id if is_group else from_phone
        attachments = data.get("attachments") or []

        # Persist last-seen timestamp for sync-on-reconnect
        from datetime import datetime, timezone
        self._last_message_at = datetime.now(timezone.utc).isoformat()

        media_urls, media_types = await self._download_attachments(attachments)
        if not text and not media_urls:
            return

        message_type = MessageType.TEXT
        if media_types:
            for prefix, mt in _MEDIA_KIND.items():
                if media_types[0].startswith(prefix):
                    message_type = mt
                    break
            else:
                message_type = MessageType.DOCUMENT

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type="group" if is_group else "dm",
            user_id=from_phone,
            user_name=from_phone,
            chat_id_alt=chat_record_id or None,
        )
        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            message_id=message_id,
            raw_message=data,
            media_urls=media_urls,
            media_types=media_types,
        )

        # Mark read + clear typing on DM only (server doesn't support these for groups)
        if not is_group and self._ws is not None:
            try:
                await self._ws.send({"type": "read", "to": from_phone})
            except Exception:
                pass

        await self.handle_message(event)

    async def _download_attachments(
        self,
        attachments: List[Dict[str, Any]],
    ) -> Tuple[List[str], List[str]]:
        if not attachments or self._http is None:
            return [], []
        urls: List[str] = []
        types: List[str] = []
        for att in attachments:
            url = str(att.get("url") or "").strip()
            mime = str(att.get("mimeType") or "").strip() or None
            if not url:
                continue
            try:
                resp = await self._http.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("claw_messenger: failed to download attachment %s: %s", url, exc)
                continue
            content_type = mime or resp.headers.get("content-type") or "application/octet-stream"
            data = resp.content
            if len(data) > MAX_INBOUND_MEDIA_SIZE:
                logger.warning(
                    "claw_messenger: attachment %s exceeds %d bytes — skipping",
                    url, MAX_INBOUND_MEDIA_SIZE,
                )
                continue
            stored_path = await self._cache_blob(data, content_type)
            if stored_path:
                urls.append(stored_path)
                types.append(content_type)
        return urls, types

    async def _cache_blob(self, blob: bytes, content_type: str) -> Optional[str]:
        try:
            if content_type.startswith("image/"):
                ext = _ext_for_mime(content_type, default=".jpg")
                return cache_image_from_bytes(blob, ext)
            if content_type.startswith("audio/"):
                ext = _ext_for_mime(content_type, default=".m4a")
                return cache_audio_from_bytes(blob, ext)
            # cache_document_from_bytes wants a filename, not just an extension
            ext = _ext_for_mime(content_type, default=".bin")
            filename = f"attachment_{int(asyncio.get_running_loop().time())}{ext}"
            return cache_document_from_bytes(blob, filename)
        except Exception:
            logger.exception("claw_messenger: failed to cache inbound media")
            return None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_MIME_EXT: Dict[str, str] = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/heic": ".heic",
    "image/heif": ".heif",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
    "audio/opus": ".opus", "audio/aac": ".aac",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "application/pdf": ".pdf",
}


def _ext_for_mime(content_type: str, *, default: str) -> str:
    primary = (content_type or "").split(";", 1)[0].strip().lower()
    return _MIME_EXT.get(primary, default)
