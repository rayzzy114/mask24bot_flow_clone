COINS: dict[str, dict[str, str]] = {
    "btc": {"symbol": "BTC", "title": "BTC", "binance": "BTCRUB"},
    "ltc": {"symbol": "LTC", "title": "LTC", "binance": "LTCUSDT"},
    "xmr": {"symbol": "XMR", "title": "XMR", "binance": "XMRUSDT"},
    "usdt": {"symbol": "USDT", "title": "USDT", "binance": "USDTRUB"},
}

DEFAULT_LINKS = {
    "faq": "https://t.me/mnIn_news",
    "channel": "https://t.me/mnIn_news",
    "chat": "https://t.me/mnln_24",
    "reviews": "https://t.me/mnIn_news",
    "review_form": "https://t.me/mnln_24",
    "manager": "https://t.me/MNLN_24",
    "operator": "https://t.me/mnln_24",
    "terms": "https://t.me/mnIn_news",
}

LINK_LABELS = {
    "faq": "FAQ",
    "channel": "Канал",
    "chat": "Чат",
    "reviews": "Отзывы",
    "review_form": "Оставить отзыв",
    "manager": "Менеджер",
    "operator": "Оператор",
    "terms": "Условия",
}

FALLBACK_RATES = {
    "btc": 7_100_000.0,
    "eth": 180_000.0,
    "ltc": 11_000.0,
    "xmr": 20_000.0,
    "trx": 12.0,
    "ton": 520.0,
    "usdt": 105.0,
}

DEFAULT_PAYMENT_METHODS = [
    "Перевод на карту",
    "СБП",
]

SELL_WALLET_LABELS = {
    "btc": "BTC",
    "ltc": "LTC",
    "usdt_trc20": "USDT (TRC20)",
    "usdt_bsc": "USDT (BSC)",
    "eth": "ETH / EVM",
    "trx": "TRX",
    "xmr": "XMR",
    "ton": "TON",
}

DEFAULT_SELL_WALLETS = {
    key: ""
    for key in SELL_WALLET_LABELS
}

PAYMENT_PROOF_PROMPT = "📸 <b>Прикрепите фото успешной оплаты. После отправки фото будет отправлено администратору.</b>"
PAYMENT_PROOF_NEED_PHOTO = "Прикрепите именно фото успешной оплаты (чек/скрин)."
PAYMENT_PROOF_SENT = "✅ <b>Фото получено. Заявка передана администратору.</b>"
PAYMENT_PROOF_STORED = "✅ Фото получено. Админы пока не настроены, заявка сохранена локально."
