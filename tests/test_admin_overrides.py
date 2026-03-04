from __future__ import annotations

import re
from pathlib import Path

from app.catalog import FlowCatalog
from app.overrides import RuntimeOverrides, apply_state_overrides

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"

CARD_RE = re.compile(r"\b\d{4}(?:[ \-]?\d{4}){3}\b")


def _catalog() -> FlowCatalog:
    return FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)


def test_operator_override_global() -> None:
    catalog = _catalog()
    assert catalog.operator_url_aliases
    assert catalog.operator_handle_aliases

    overrides = RuntimeOverrides(
        operator_url="https://t.me/new_operator_777",
        payment_requisites="",
    )

    replaced_in_buttons = 0
    replaced_in_text = 0

    for state in catalog.states.values():
        updated = apply_state_overrides(
            state=state,
            overrides=overrides,
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
        )

        for row in updated.get("button_rows") or []:
            for btn in row:
                if btn.get("url") == "https://t.me/new_operator_777":
                    replaced_in_buttons += 1

        text_blob = "\n".join(
            [
                str(updated.get("text") or ""),
                str(updated.get("text_html") or ""),
                str(updated.get("text_markdown") or ""),
            ]
        )
        if "@new_operator_777" in text_blob or "https://t.me/new_operator_777" in text_blob:
            replaced_in_text += 1

    assert replaced_in_buttons > 0
    assert replaced_in_text > 0


def test_requisites_override_global() -> None:
    catalog = _catalog()
    assert catalog.detected_requisites

    new_requisites = "5555 6666 7777 8888"
    overrides = RuntimeOverrides(
        operator_url="",
        payment_requisites=new_requisites,
    )

    replaced_states = 0
    for state in catalog.states.values():
        text_blob = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        )
        had_cards = bool(CARD_RE.search(text_blob))
        updated = apply_state_overrides(
            state=state,
            overrides=overrides,
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
        )
        updated_blob = "\n".join(
            [
                str(updated.get("text") or ""),
                str(updated.get("text_html") or ""),
                str(updated.get("text_markdown") or ""),
            ]
        )

        for old_card in catalog.detected_requisites:
            assert old_card not in updated_blob

        if had_cards:
            assert new_requisites in updated_blob
            replaced_states += 1

    assert replaced_states > 0


def test_sell_wallet_override_global() -> None:
    catalog = _catalog()
    aliases = {key: values for key, values in catalog.sell_wallet_aliases.items() if values}
    assert aliases

    overrides = RuntimeOverrides(
        operator_url="",
        payment_requisites="",
        sell_wallet_overrides={
            "ltc": "LTCSellWallet111111111111111111111111111",
            "usdt_trc20": "TXySellWallet11111111111111111111111",
            "usdt_bsc": "0x1111111111111111111111111111111111111111",
        },
    )

    replaced_hits = 0
    for state in catalog.states.values():
        source_blob = "\n".join(
            [
                str(state.get("text") or ""),
                str(state.get("text_html") or ""),
                str(state.get("text_markdown") or ""),
            ]
        )
        updated = apply_state_overrides(
            state=state,
            overrides=overrides,
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
            sell_wallet_aliases=catalog.sell_wallet_aliases,
        )
        updated_blob = "\n".join(
            [
                str(updated.get("text") or ""),
                str(updated.get("text_html") or ""),
                str(updated.get("text_markdown") or ""),
            ]
        )

        for key, replacement in overrides.sell_wallet_overrides.items():
            key_aliases = catalog.sell_wallet_aliases.get(key, ())
            if not key_aliases:
                continue
            if any(alias in source_blob for alias in key_aliases):
                assert replacement in updated_blob
                for alias in key_aliases:
                    assert alias not in updated_blob
                replaced_hits += 1

    assert replaced_hits > 0
