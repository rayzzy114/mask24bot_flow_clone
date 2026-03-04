from app.overrides import _replace_sell_wallets

def test():
    # Simulate a sell wallet replacement in Markdown/HTML
    text = "Please send funds to: bc1q... [Wallet](bc1q...) or <b>bc1q...</b>"
    sell_wallet_overrides = {"btc": "bc1q_new_wallet_123*"}
    sell_wallet_aliases = {"btc": ("bc1q...",)}

    result = _replace_sell_wallets(text, sell_wallet_overrides=sell_wallet_overrides, sell_wallet_aliases=sell_wallet_aliases)
    print("Original:", text)
    print("Result:", result)

test()
