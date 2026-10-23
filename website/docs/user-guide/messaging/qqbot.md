# QQ Bot

Python dependency commands on this page use a
[PM-prepared source checkout](../../reference/package-management.md#developer-workflow).
After a dependency change, reactivate the checkout and restart Hermes.

Connect Hermes to QQ via the **Official QQ Bot API (v2)** — supporting private (C2C), group @-mentions, guild, and direct messages with voice transcription.

## Overview

The QQ Bot adapter uses the [Official QQ Bot API](https://bot.q.qq.com/wiki/develop/api-v2/) to:

- Receive messages via a persistent **WebSocket** connection to the QQ Gateway
- Send text and markdown replies via the **REST API**
- Download and process images, voice messages, and file attachments
- Transcribe voice messages using Tencent's built-in ASR or a configurable STT provider

## Prerequisites

1. **QQ Bot Application** — Register at [q.qq.com](https://q.qq.com):
   - Create a new application and note your **App ID** and **App Secret**
   - Enable the required intents: C2C messages, Group @-messages, Guild messages
   - Configure your bot in sandbox mode for testing, or publish for production

2. **Dependencies** — The adapter requires `aiohttp` and `httpx`:
   ```bash
   python -c "import pm; pm.sync_venv(['messaging'], explicit=True)"
   ```

## Configuration

### Interactive setup

```bash
hermes gateway setup
```

Select **QQ Bot** from the platform list and follow the prompts.

### Manual configuration

Set the required environment variables in `~/.hermes/.env`:

```bash
QQ_APP_ID=your-app-id
QQ_CLIENT_SECRET=your-app-secret
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `QQ_APP_ID` | QQ Bot App ID (required) | — |
| `QQ_CLIENT_SECRET` | QQ Bot App Secret (required) | — |
| `QQBOT_HOME_CHANNEL` | OpenID for cron/notification delivery | — |
| `QQBOT_HOME_CHANNEL_NAME` | Display name for home channel | `Home` |
| `QQ_ALLOWED_USERS` | Comma-separated user OpenIDs for DM access | open (all users) |
| `QQ_GROUP_ALLOWED_USERS` | Comma-separated group OpenIDs for group access | — |
| `QQ_ALLOW_ALL_USERS` | Set to `true` to allow all DMs | `false` |
| `QQ_PORTAL_HOST` | Override the QQ portal host (set to `sandbox.q.qq.com` for sandbox routing) | `q.qq.com` |
| `QQ_STT_API_KEY` | API key for voice-to-text provider | — |
| `QQ_STT_BASE_URL` | (Not read directly — set `platforms.qqbot.extra.stt.baseUrl` in `config.yaml` instead) | n/a |
| `QQ_STT_MODEL` | STT model name | `glm-asr` |

## Advanced Configuration

For fine-grained control, add platform settings to `~/.hermes/config.yaml`:

```yaml
platforms:
  qqbot:
    enabled: true
    extra:
      app_id: "your-app-id"
      client_secret: "your-secret"
      markdown_support: true       # enable QQ markdown (msg_type 2). Config-only; no env-var equivalent.
      dm_policy: "open"          # open | allowlist | disabled
      allow_from:
        - "user_openid_1"
      group_policy: "open"       # open | allowlist | disabled
      group_allow_from:
        - "group_openid_1"
      stt:
        provider: "zai"          # zai (GLM-ASR), openai (Whisper), etc.
        baseUrl: "https://open.bigmodel.cn/api/coding/paas/v4"
        apiKey: "your-stt-key"
        model: "glm-asr"
        timeout: 60              # seconds per transcription request (default 60)
```

## Full group archive with mention-only replies

The QQ group owner must enable **Receive all group messages** for this bot in each group.
QQ then emits `GROUP_MESSAGE_CREATE` under the same `GROUP_AND_C2C_EVENT` intent.
Enable bounded background storage separately in the owning Hermes profile:

```yaml
platforms:
  qqbot:
    extra:
      group_context:
        enabled: true
        archive_enabled: true    # keep all received messages and original files
        max_messages: 100        # recent prompt window only
        max_age_hours: 24
        max_context_chars: 16000
        max_message_chars: 2000
```

Restart the gateway after changing this configuration. Existing group allowlists still apply.
Ordinary messages never call the model or trigger replies. With `archive_enabled: true`, each
received event is saved in full and all delivered attachments are downloaded as original bytes,
including images, video, documents and audio. Audio is archived without transcription or conversion.
Successful bot replies and bot media are also archived. This mode is opt-in; the default false keeps
the lightweight bounded background mode with attachment references and on-demand retrieval.

Full archives live in the owning profile's `cache/qqbot/<app-key>/group-archive/<group-key>/`:
`messages/*.json` preserves full message data; `messages.jsonl` indexes messages and real local file
paths; `files/` stores originals; `members/` stores observed profiles and avatars. Each message and
file is tied to the group, message ID, stable sender ID, display name and time. Equal display names
do not merge identities. Directories are private (0700), files are 0600, and message files are
atomically replaced. No archive retention limit is applied. Failed downloads are recorded, and
interrupted pending records are recovered on the next startup or group event.

The `max_messages`, `max_age_hours`, `max_message_chars` and `max_context_chars` settings bound only
the recent prompt window; they never delete full archives. On a mention, the model receives recent
messages with file paths and sender identity, up to three recent archived images through the image
pipeline, the current speaker's identity/profile, and this group's complete index path for reading
older history or other files with its tools. No media keywords are required. Profiles, bots and
groups remain isolated. Per-user agent conversations remain independent.

QQ's `mentions[].is_you` marker handles group-scoped IDs even without a separate AT event. Dual
events are deduplicated for replies. A bare mention naturally joins the current discussion;
background does not request a transcript summary. Only explicit requests call for summaries or
long explanations. `/new` resets the individual conversation, not the group's full archive.

Available profile fields retain their QQ provenance. Nicknames, stable IDs, roles and delivered
avatar URLs are recorded; OpenID avatars are cached per member. Signatures are saved only when QQ
provides them, otherwise marked `not_provided_by_qq` (unknown, not an empty signature). Avatars and
profile data are background, never instructions.

The existing bounded cache is imported on first use/startup, including recoverable attachment
links. Messages or file links that QQ never delivered or an older version discarded cannot be
reconstructed. Expired links are marked failed rather than represented as downloaded files.
Disable `enabled` or `archive_enabled` to stop new full archiving; existing files are retained.

## Voice Messages (STT)

Voice transcription works in two stages:

1. **QQ built-in ASR** (free, always tried first) — QQ provides `asr_refer_text` in voice message attachments, which uses Tencent's own speech recognition
2. **Configured STT provider** (fallback) — If QQ's ASR doesn't return text, the adapter calls an OpenAI-compatible STT API:

   - **Zhipu/GLM (zai)**: Default provider, uses `glm-asr` model
   - **OpenAI Whisper**: Set `QQ_STT_BASE_URL` and `QQ_STT_MODEL`
   - Any OpenAI-compatible STT endpoint

## Troubleshooting

### Bot disconnects immediately (quick disconnect)

This usually means:
- **Invalid App ID / Secret** — Double-check your credentials at q.qq.com
- **Missing permissions** — Ensure the bot has the required intents enabled
- **Sandbox-only bot** — If the bot is in sandbox mode, it can only receive messages from QQ's sandbox test channel

### Voice messages not transcribed

1. Check if QQ's built-in `asr_refer_text` is present in the attachment data
2. If using a custom STT provider, verify `QQ_STT_API_KEY` is set correctly
3. Check gateway logs for STT error messages

### Messages not delivered

- Verify the bot's **intents** are enabled at q.qq.com
- Check `QQ_ALLOWED_USERS` if DM access is restricted
- For group messages, ensure the bot is **@mentioned** (group policy may require allowlisting)
- Check `QQBOT_HOME_CHANNEL` for cron/notification delivery

### Connection errors

- Ensure `aiohttp` and `httpx` are installed: `python -c "import pm; pm.sync_venv(['messaging'], explicit=True)"`
- Check network connectivity to `api.sgroup.qq.com` and the WebSocket gateway
- Review gateway logs for detailed error messages and reconnect behavior
