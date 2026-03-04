from app.utils import parse_amount, parse_non_negative_amount


def test_parse_non_negative_amount_accepts_zero() -> None:
    assert parse_non_negative_amount("0") == 0.0
    assert parse_non_negative_amount("0.0") == 0.0
    assert parse_non_negative_amount("0,00") == 0.0


def test_parse_amount_still_rejects_zero() -> None:
    assert parse_amount("0") is None
    assert parse_amount("0.0") is None
