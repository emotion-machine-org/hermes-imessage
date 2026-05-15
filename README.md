# hermes-imessage

iMessage, RCS, and SMS for [Hermes Agent](https://hermes-agent.nousresearch.com/). No Mac required, no SIM required. Powered by the [Claw Messenger](https://clawmessenger.com) relay.

Your Hermes agent can:

- Receive iMessages / RCS / SMS sent to your Claw Messenger number.
- Reply back as your number.
- Send to one-on-one chats (DMs) and existing group chats.
- Create new group chats by calling the `claw_messenger_create_group` tool.
- Receive image / video / audio / document attachments — they land as cached local files the agent's vision tools can read.

## Quick start

```bash
# 1. Install into the Hermes venv.
#    Hermes (curl-installer) ships a uv-managed venv with no system pip,
#    so use uv with --python pointing at it:
uv pip install --python ~/.hermes/hermes-agent/venv hermes-imessage

# 2. Enable the plugin in ~/.hermes/config.yaml.
#    `hermes plugins enable` cannot currently see entry-point plugins, so
#    edit config.yaml directly (or run the one-liner below):
python3 -c "
from pathlib import Path
p = Path.home() / '.hermes' / 'config.yaml'
text = p.read_text() if p.exists() else ''
if 'imessage' not in text:
    p.write_text(text.rstrip() + '\n\nplugins:\n  enabled:\n    - imessage\n')
"

# 3. Enable the agent toolset so claw_messenger_create_group is reachable
hermes tools enable hermes-claw-messenger

# 4. Map the toolset onto the claw_messenger platform (merge with existing
#    platform_toolsets:; do not replace other entries).
#    Adds the block:
#      platform_toolsets:
#        claw_messenger:
#          - hermes-claw-messenger

# 5. Configure the API key (from https://clawmessenger.com/dashboard)
echo 'CLAW_MESSENGER_API_KEY=cm_live_…' >> ~/.hermes/.env

# 6. Start the gateway
hermes gateway start
```

Then register one or more phone numbers on the Claw Messenger dashboard so inbound messages reach your tenant.

## Configuration

All settings can be supplied via environment variables (preferred) or `~/.hermes/config.yaml`.

| Env var | Default | Purpose |
|---|---|---|
| `CLAW_MESSENGER_API_KEY` | — | **Required.** From clawmessenger.com/dashboard. |
| `CLAW_MESSENGER_SERVER_URL` | `wss://claw-messenger.onrender.com` | WebSocket URL of the relay. |
| `CLAW_MESSENGER_PREFERRED_SERVICE` | `iMessage` | `iMessage` / `RCS` / `SMS`. |
| `CLAW_MESSENGER_ALLOWED_USERS` | — | Comma-separated allowlist (E.164 phones or group chatIds). |
| `CLAW_MESSENGER_ALLOW_ALL_USERS` | `false` | If `true`, any sender can talk to the bot. |
| `CLAW_MESSENGER_HOME_CHANNEL` | — | Default phone or chatId for cron jobs with `deliver=claw_messenger`. |

YAML equivalent:

```yaml
gateway:
  platforms:
    claw_messenger:
      enabled: true
      extra:
        api_key: cm_live_…             # also accepts CLAW_MESSENGER_API_KEY env
        preferred_service: iMessage
        home_channel: "+15551234567"

plugins:
  enabled:
    - imessage
```

## Agent tools

| Tool | Use |
|---|---|
| `claw_messenger_create_group` | Create a new iMessage/RCS/SMS group with two or more E.164 phones and send the first message. Returns the `chat_id` to use for follow-up sends. |

Outbound DMs and replies to existing groups don't need a tool — Hermes routes them through the standard messaging flow.

## Cron delivery (out-of-process)

```yaml
cron:
  jobs:
    - name: morning-summary
      schedule: "0 9 * * 1-5"
      prompt: "Send me the day's calendar"
      deliver: claw_messenger     # → CLAW_MESSENGER_HOME_CHANNEL
```

When `hermes cron run` executes in a separate process from `hermes gateway`, the plugin's `standalone_sender_fn` opens a one-shot WebSocket connection, delivers the message, and closes. No gateway dependency.

## Development

```bash
git clone https://github.com/emotion-machine-org/hermes-imessage.git
cd hermes-imessage
pip install -e ".[dev]"
pytest tests/                              # 41 unit tests, ~7s
```

Live smoke test against the production relay (needs a real API key):

```bash
CLAW_MESSENGER_API_KEY=cm_live_… python -c "
import asyncio
from gateway.config import PlatformConfig
from hermes_claw_messenger.standalone import standalone_send
asyncio.run(standalone_send(PlatformConfig(enabled=True, extra={}),
                            '+15551234567', 'live test'))
"
```

## Naming note

The PyPI package is `hermes-imessage` for discoverability. The internal Python module
is `hermes_claw_messenger` (matching the underlying Claw Messenger relay's brand) and
the Hermes platform identifier is `claw_messenger`. Both can appear in logs and config —
they are the same plugin.

## License

MIT
