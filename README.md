# hermes-claw-messenger

iMessage, RCS & SMS messaging platform for [Hermes Agent](https://hermes-agent.nousresearch.com/) via the [Claw Messenger](https://clawmessenger.com) relay. No Mac required, no SIM required — just a Claw Messenger API key.

## Install

```bash
pip install hermes-claw-messenger
hermes plugins enable claw-messenger
```

Or via git:

```bash
hermes plugins install emotion-machine-org/hermes-claw-messenger
```

## Configure

```bash
export CLAW_MESSENGER_API_KEY=cm_live_XXXXXXXX_YYYYYYYYY     # from https://clawmessenger.com/dashboard
hermes gateway start
```

Then register one or more phone numbers your agent should receive messages on:

```bash
curl -X POST https://claw-messenger.onrender.com/api/phone-routes \
  -H "Authorization: Bearer $CLAW_MESSENGER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "+15551234567"}'
```

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `CLAW_MESSENGER_API_KEY` | — | Required. API key from dashboard. |
| `CLAW_MESSENGER_SERVER_URL` | `wss://claw-messenger.onrender.com` | WebSocket URL. |
| `CLAW_MESSENGER_PREFERRED_SERVICE` | `iMessage` | `iMessage` / `RCS` / `SMS`. |
| `CLAW_MESSENGER_ALLOWED_USERS` | — | Comma-separated allowlist (phones or group chatIds). |
| `CLAW_MESSENGER_ALLOW_ALL_USERS` | `false` | If `true`, any sender can talk to the bot. |
| `CLAW_MESSENGER_HOME_CHANNEL` | — | Default phone or chatId for `cron deliver=claw_messenger`. |

## Capabilities

- Send / receive text DMs (E.164 phone numbers)
- Send / receive group chat messages (Claw Messenger `chatId`)
- Send / receive media attachments (images, voice, video, documents)
- Typing indicators (DM only)
- Tool: `claw_messenger_create_group` — create a new group with 2+ phones and send the first message

## License

MIT
