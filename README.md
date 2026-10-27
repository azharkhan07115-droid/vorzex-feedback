# VORZEX Feedback Proxy

This Render web service receives an authenticated PNG and real launch/win metadata from the
VORZEX Android app, then posts it to Telegram. The Telegram bot token and channel destination
remain on Render and are never shipped in the APK.

## Deploy on Render

1. Put this folder in a private GitHub repository.
2. In Render choose **New > Blueprint** and select the repository, or create a Python Web Service.
3. Set these secret environment variables in the Render dashboard:
   - `TELEGRAM_BOT_TOKEN`: rotate the token previously pasted in chat, then use the new token.
   - `TELEGRAM_CHAT_ID`: the numeric ID or `@username` of the new feedback channel.
   - `VORZEX_FEEDBACK_APP_SECRET`: paste the exact value from the separately supplied
     `VORZEX_FEEDBACK_APP_SECRET.txt`. Do not generate a different value on Render.
4. Deploy and open `/health`. It must return `{"configured":true,"ok":true}`.
5. This v18 APK is configured for `https://vorzex-feedback-py.onrender.com/feedback`. Keep the Web
   Service name `vorzex-feedback-py`. If Render assigns a different public hostname, regenerate the
   native blobs with that exact `/feedback` URL and rebuild the APK. The Telegram token and channel
   ID are never included in the APK.

After deployment, configure UptimeRobot to send a simple HTTPS GET to
`https://vorzex-feedback-py.onrender.com/health` every 10 minutes. Do not enter the `/feedback` URL in
UptimeRobot. The Blueprint intentionally creates only the free Web Service; it does not create a
paid Render Cron Service. `keep_alive.py` remains as an optional standalone helper but is not used by
the Blueprint.

UptimeRobot continues even if the phone is switched off or Android kills the APK. The health check
sends no screenshot, caption or Telegram message.

## Request contract used by the APK

`POST /feedback` as `multipart/form-data`:

- headers: `X-Vorzex-Version`, `X-Vorzex-Timestamp`, `X-Vorzex-Nonce`,
  `X-Vorzex-Signature`
- fields: `kind` (`launch` or `win`), `caption` (real app/match/device/date data)
- file: `photo` (PNG, maximum 10 MiB)

Signature payload:

```text
version + "\n" + timestamp + "\n" + nonce + "\n" + kind + "\n" + sha256(photo) + "\n" + caption
```

The signature is HMAC-SHA256 with `VORZEX_FEEDBACK_APP_SECRET`. Version 18 is required. Because
the image hash, caption and feedback type are inside the signature, copying valid headers onto a
custom picture or edited caption is rejected. The server also validates time,
nonce replay, rate, PNG structure/dimensions and caption length. It never logs the token, channel,
caption or image. The screenshot exists only in request memory and is not written to disk.

Captions use VORZEX's own mathematical-bold Unicode typography and direct JSON
`caption_entities` with UTF-16 offsets. The server reports `premium_emoji: true` only when Telegram
accepts the custom entities; otherwise it retries once with the same styled caption and ordinary
Unicode emoji, so the post is never reduced to plain text.

Telegram's Bot API permits custom emoji in channel posts only for eligible bots that purchased an
additional username on Fragment. A Premium subscription owned by the bot owner covers direct bot
messages to private chats, groups and supergroups, but not channels. This is a Telegram-side account
restriction and cannot be bypassed by changing an emoji ID or parse mode.
