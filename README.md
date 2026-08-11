# Gifts Intelligence

Commercial Telegram subscription bot for:
- Gifts Intelligence NFT gift signals
- NFT-Tracker: @fyvfhvfhyfbot
- Telegram Stars payments
- CryptoBot USDT payments
- Supabase subscriptions/users/payments
- 3-signal demo from a private signal channel
- Per-user invite links
- Admin CRM, support, replies, broadcasts and statistics

## Security

No secrets belong in GitHub. Put all credentials into Railway Variables.

Required Railway variables:
- BOT_TOKEN
- SIGNAL_CHANNEL_ID
- ADMIN_USERNAMES
- TRACKER_BOT_USERNAME
- SUPABASE_URL
- SUPABASE_KEY
- CRYPTOBOT_API_TOKEN

See `.env.example`.

## Telegram channel setup

Add the subscription bot as administrator to the private signal channel.

Required permission:
- Invite Users via Link

The bot also needs to receive channel posts, which Telegram provides to a bot that is an administrator of the channel.

The bot creates unique invite links after successful payment and revokes them after subscription expiry.

## Supabase

Open `supabase/schema.sql` in the Supabase SQL Editor and run it once.

Use the Supabase service-role key only on the server/Railway. Never expose it to a client.

## Demo

A user presses "🎁 Get 3 free signals".

The bot stores an active demo session. The next three new `channel_post` updates from the private signal channel are copied to that user. Old channel history is not scraped.

## Payments

Stars:
- currency XTR
- successful_payment activates the subscription

Crypto:
- Crypto Pay API
- USDT invoices
- the bot polls pending invoices every `CRYPTO_POLL_SECONDS`
- paid invoices activate the subscription

Polling is intentional so the first version does not require a public webhook endpoint.

## Run locally

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Railway

Deploy this repository as a Docker project and add the environment variables from `.env.example`.

Do not commit `.env`.

## Admin

Admin access is based on Telegram usernames from `ADMIN_USERNAMES`.

Example:
`ADMIN_USERNAMES=skillell`

For production, after the bot is running, use the admin panel to inspect users and subscriptions.


## Admin notifications

`ADMIN_USERNAMES=skillell` is enough for admin authentication.

Because Telegram Bot API does not provide a general "send message to @username" operation,
the bot automatically registers the admin's chat ID when the admin sends `/start` to the bot.
For persistence across restarts, you may also set `ADMIN_CHAT_IDS=<numeric_id>` in Railway.

No admin secret is stored in GitHub.

## Crypto payment polling

The bot checks paid Crypto Pay invoices periodically and uses the payment row in Supabase
for idempotency. A paid invoice cannot activate the same payment twice.
"# gifts-intelligencece" 
