# mask24bot_flow_clone

Strict clone project generated from captured flow artifacts in `data/raw` and `data/media`.
AdminKit is embedded directly in this bot project (`app/handlers/admin.py` + `app/storage.py` + related modules in `app/`).
Operator/requisites overrides are runtime-global (buttons + text + formatted text).
Rate updates are pulled from CoinGecko and applied to all rate lines in rendered states.

## Run

1. Copy `.env.example` to `.env`.
2. Set `BOT_TOKEN`.
3. Set `ADMIN_IDS` as comma-separated Telegram user IDs (optional but needed to receive payment proofs in admin chats).
4. Run: `uv run main.py`

## Admin Panel

- `/admin` opens the full inline AdminKit panel (commission, env editor, links, requisites mode/value/bank, payment methods, rates refresh).
- `/admin` also includes sell-crypto wallet management (`🪙 Кошельки продажи`) so destination crypto wallets can be changed from admin panel.
- Order confirmation callback flow is enabled via `admin:order:confirm:*`.
- Payment proof photo flow (`✅ Я оплатил`) forwards to admins and creates a paid order in embedded admin storage.

## Tests

Run: `uv run --group dev pytest`
