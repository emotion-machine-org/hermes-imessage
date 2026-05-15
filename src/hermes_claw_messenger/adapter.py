"""Claw Messenger platform adapter — stub. Real implementation lands next step."""

from __future__ import annotations

import logging
from typing import Any, Dict

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult

logger = logging.getLogger(__name__)


def check_requirements() -> bool:
    """Verify runtime deps are importable."""
    try:
        import websockets  # noqa: F401
        import httpx  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config: PlatformConfig) -> bool:
    """Basic config validation. Called before adapter construction."""
    import os
    return bool(os.getenv("CLAW_MESSENGER_API_KEY"))


class ClawMessengerAdapter(BasePlatformAdapter):
    """Stub adapter — connect/send not yet implemented."""

    MAX_MESSAGE_LENGTH = 10000

    def __init__(self, config: PlatformConfig, **kwargs):
        platform = Platform("claw_messenger")
        super().__init__(config=config, platform=platform)

    @property
    def name(self) -> str:
        return "Claw Messenger"

    async def connect(self) -> bool:
        self._set_fatal_error("not_implemented", "Adapter not yet implemented", retryable=False)
        return False

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        return SendResult(success=False, error="not implemented")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm", "chat_id": chat_id}
