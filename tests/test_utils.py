from app.utils import is_valid_crypto_address, parse_amount, parse_non_negative_amount


def test_parse_non_negative_amount_accepts_zero() -> None:
    assert parse_non_negative_amount("0") == 0.0
    assert parse_non_negative_amount("0.0") == 0.0
    assert parse_non_negative_amount("0,00") == 0.0


def test_parse_amount_still_rejects_zero() -> None:
    assert parse_amount("0") is None
    assert parse_amount("0.0") is None


def test_usdt_address_validation_respects_network_hint() -> None:
    trx_like = "TGE2wN657wEEDwUoB2w1y7g9x7xX4fD8gK"
    evm_like = "0x1111111111111111111111111111111111111111"
    assert is_valid_crypto_address(evm_like, "USDT", network_hint="USDT BSC20")
    assert not is_valid_crypto_address(trx_like, "USDT", network_hint="USDT BSC20")
    assert not is_valid_crypto_address(evm_like, "USDT", network_hint="USDT TRC20")


def test_usdt_trc20_hint_accepts_valid_trx_when_tether_word_present(monkeypatch) -> None:
    trx_like = "TXStubValidAddress1111111111111111111"
    evm_like = "0x1111111111111111111111111111111111111111"

    monkeypatch.setattr(
        "app.utils.validate_base58_checksum",
        lambda address: address == trx_like,
    )

    hint = "USDT TETHER TRC20"
    assert is_valid_crypto_address(trx_like, "USDT", network_hint=hint)
    assert not is_valid_crypto_address(evm_like, "USDT", network_hint=hint)


def test_ltc_bech32_address_is_accepted() -> None:
    assert is_valid_crypto_address("ltc1qzm85lrk2nh4gq8s2jvewajwnc5x98gkke3v2gc", "LTC")


def test_ton_user_friendly_address_is_accepted() -> None:
    assert is_valid_crypto_address("UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ", "TON")
