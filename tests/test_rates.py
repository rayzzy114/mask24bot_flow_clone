from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.catalog import FlowCatalog
from app.overrides import RuntimeOverrides, apply_state_overrides
from app.rates import RateService

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"


async def _fetch_rates() -> dict[str, float]:
    payload = {
        "bitcoin": {"rub": 9023456.12},
        "litecoin": {"rub": 7543.21},
        "tether": {"rub": 94.87},
        "tron": {"rub": 10.0},
        "ethereum": {"rub": 200000.0},
        "monero": {"rub": 15000.0},
        "the-open-network": {"rub": 200.0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=json.dumps(payload))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = RateService(http_client=client)
    return await service.fetch_rates()


def test_rates_from_coingecko_global() -> None:
    rates = asyncio.run(_fetch_rates())
    assert rates["btc"] == 9023456.12
    assert rates["ltc"] == 7543.21
    assert rates["usdt"] == 94.87


def test_live_rates_apply_to_all_rate_screens() -> None:
    catalog = FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)
    live_rates = {
        "BTC": 8_999_999.99,
        "LTC": 7_654.32,
        "USDT": 101.45,
        "ETH": 420_001.11,
        "TRX": 9.87,
        "TON": 333.21,
    }
    empty_overrides = RuntimeOverrides(operator_url="", payment_requisites="")

    hits = 0
    for state in catalog.states.values():
        source_text = str(state.get("text") or "")
        if "Курс покупки" not in source_text and "Курс продажи" not in source_text:
            continue

        updated = apply_state_overrides(
            state=state,
            overrides=empty_overrides,
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
            live_rates_rub=live_rates,
        )
        updated_text = str(updated.get("text") or "")

        if "USDT" in source_text or "Tether" in source_text:
            assert "101 руб." in updated_text
            hits += 1
        elif "Litecoin" in source_text or "LTC" in source_text:
            assert "7 654 руб." in updated_text
            hits += 1
        elif "Bitcoin" in source_text or "BTC" in source_text:
            assert "9 000 000 руб." in updated_text
            hits += 1

    assert hits > 0


def test_live_rates_apply_commission_for_buy_and_sell() -> None:
    state = {
        "text": (
            "📉 Курс покупки Bitcoin (₿): 100.00 руб.\n"
            "📈 Курс продажи Bitcoin (₿): 100.00 руб."
        ),
        "text_html": "",
        "text_markdown": "",
        "button_rows": [],
    }
    updated = apply_state_overrides(
        state=state,
        overrides=RuntimeOverrides(
            operator_url="",
            payment_requisites="",
            commission_percent=2.5,
        ),
        operator_url_aliases=(),
        operator_handle_aliases=(),
        detected_requisites=(),
        live_rates_rub={"BTC": 100.0},
    )
    text = str(updated.get("text") or "")
    assert "Курс покупки Bitcoin (₿): 102 руб." in text
    assert "Курс продажи Bitcoin (₿): 98 руб." in text


def test_commission_updates_payment_lines_plain_html_markdown() -> None:
    state = {
        "text": (
            "📉 Курс покупки Bitcoin (₿): 100.00 руб.\n"
            "К оплате: 1 000 руб.\n"
            "С учетом скидки: 900 руб."
        ),
        "text_html": (
            "📉 Курс покупки <strong>Bitcoin</strong> (₿): <code>100.00 руб.</code>\n"
            "<strong>К оплате:</strong> <del>1 000 руб.</del>\n"
            "<strong>С учетом скидки</strong>: 900 руб."
        ),
        "text_markdown": (
            "📉 Курс покупки **Bitcoin** (₿): `100.00 руб.`\n"
            "**К оплате:** ~~1 000 руб.~~\n"
            "**С учетом скидки**: 900 руб."
        ),
        "button_rows": [],
    }
    updated = apply_state_overrides(
        state=state,
        overrides=RuntimeOverrides(
            operator_url="",
            payment_requisites="",
            commission_percent=10.0,
        ),
        operator_url_aliases=(),
        operator_handle_aliases=(),
        detected_requisites=(),
        live_rates_rub={"BTC": 100.0},
    )

    text = str(updated.get("text") or "")
    text_html = str(updated.get("text_html") or "")
    text_markdown = str(updated.get("text_markdown") or "")

    assert "Курс покупки Bitcoin (₿): 110 руб." in text
    assert "К оплате: 1 100 руб." in text
    assert "С учетом скидки: 990 руб." in text

    assert "Курс покупки <strong>Bitcoin</strong> (₿): <code>110 руб.</code>" in text_html
    assert "<strong>К оплате:</strong> <del>1 100 руб.</del>" in text_html
    assert "<strong>С учетом скидки</strong>: 990 руб." in text_html

    assert "Курс покупки **Bitcoin** (₿): `110 руб.`" in text_markdown
    assert "**К оплате:** ~~1 100 руб.~~" in text_markdown
    assert "**С учетом скидки**: 990 руб." in text_markdown


def test_zero_commission_keeps_buy_sell_and_payment_lines() -> None:
    state = {
        "text": (
            "📉 Курс покупки Bitcoin (₿): 100.00 руб.\n"
            "📈 Курс продажи Bitcoin (₿): 100.00 руб.\n"
            "К оплате: 1 000 руб.\n"
            "С учетом скидки: 900 руб."
        ),
        "text_html": "",
        "text_markdown": "",
        "button_rows": [],
    }
    updated = apply_state_overrides(
        state=state,
        overrides=RuntimeOverrides(
            operator_url="",
            payment_requisites="",
            commission_percent=0.0,
        ),
        operator_url_aliases=(),
        operator_handle_aliases=(),
        detected_requisites=(),
        live_rates_rub={"BTC": 100.0},
    )
    text = str(updated.get("text") or "")
    assert "Курс покупки Bitcoin (₿): 100 руб." in text
    assert "Курс продажи Bitcoin (₿): 100 руб." in text
    assert "К оплате: 1 000 руб." in text
    assert "С учетом скидки: 900 руб." in text
