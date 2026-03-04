from app.utils import _parse_float
from app.overrides import _parse_money_value

def test_parse_float():
    assert _parse_float("1 000.5") == 1000.5
    assert _parse_float("1 000,5") == 1000.5
    assert _parse_float("1.000,50") == 1000.5
    assert _parse_float("1,000.50") == 1000.5

def test_parse_money():
    assert _parse_money_value("1 000.5") == 1000.5
    assert _parse_money_value("1 000,5") == 1000.5
    assert _parse_money_value("1.000,50") == 1000.5
    assert _parse_money_value("1,000.50") == 1000.5
