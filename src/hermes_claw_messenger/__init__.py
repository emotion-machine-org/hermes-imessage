"""Hermes Agent plugin for Claw Messenger.

Bridges iMessage / RCS / SMS via the Claw Messenger relay into the Hermes
gateway. Registers a single platform adapter named ``claw_messenger``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.1.0"


def register(ctx) -> None:
    """Plugin entry point — called by Hermes plugin loader."""
    # Lazy imports so that just *discovering* the plugin (e.g. when listing
    # available plugins) does not pull in heavy deps like ``websockets``.
    from .adapter import (
        ClawMessengerAdapter,
        check_requirements,
        validate_config,
    )
    from .setup import (
        env_enablement,
        apply_yaml_config,
        interactive_setup,
    )
    from .standalone import standalone_send

    ctx.register_platform(
        name="claw_messenger",
        label="Claw Messenger",
        adapter_factory=lambda cfg: ClawMessengerAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["CLAW_MESSENGER_API_KEY"],
        install_hint="pip install hermes-claw-messenger",
        setup_fn=interactive_setup,
        env_enablement_fn=env_enablement,
        apply_yaml_config_fn=apply_yaml_config,
        cron_deliver_env_var="CLAW_MESSENGER_HOME_CHANNEL",
        standalone_sender_fn=standalone_send,
        allowed_users_env="CLAW_MESSENGER_ALLOWED_USERS",
        allow_all_env="CLAW_MESSENGER_ALLOW_ALL_USERS",
        max_message_length=10000,
        emoji="💬",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via Claw Messenger (iMessage / RCS / SMS). "
            "Recipients see your replies as real text messages on their phone. "
            "Markdown does NOT render — use plain text. Keep replies short and "
            "conversational. Avoid links unless asked. To start a new group, call "
            "claw_messenger_create_group with at least two E.164 phone numbers."
        ),
    )
    logger.debug("hermes-claw-messenger registered (v%s)", __version__)
