from __future__ import annotations

from pathlib import Path

from app.catalog import FlowCatalog, _match_link_key
from app.overrides import RuntimeOverrides, apply_state_overrides

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"


def _catalog() -> FlowCatalog:
    return FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)


def _iter_button_urls(state: dict) -> list[str]:
    urls: list[str] = []
    for row in state.get("button_rows") or []:
        if not isinstance(row, list):
            continue
        for btn in row:
            if not isinstance(btn, dict):
                continue
            url = str(btn.get("url") or "").strip()
            if url:
                urls.append(url)
    return urls


def test_link_overrides_global_from_admin_settings() -> None:
    catalog = _catalog()
    alias_to_keys: dict[str, set[str]] = {}
    for key, aliases in catalog.link_url_aliases.items():
        if key == "operator":
            continue
        for alias in aliases:
            alias_to_keys.setdefault(alias, set()).add(key)
    non_operator_aliases = {
        key: aliases
        for key, aliases in catalog.link_url_aliases.items()
        if key != "operator"
        and aliases
        and all(len(alias_to_keys.get(alias, set())) == 1 for alias in aliases)
    }
    assert non_operator_aliases

    link_overrides = {
        key: f"https://example.com/{key}"
        for key in sorted(non_operator_aliases.keys())
    }
    overrides = RuntimeOverrides(
        operator_url="",
        payment_requisites="",
        link_overrides=link_overrides,
    )

    replaced_urls = 0
    for state in catalog.states.values():
        updated = apply_state_overrides(
            state=state,
            overrides=overrides,
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
            link_url_aliases=catalog.link_url_aliases,
        )

        source_urls = set(_iter_button_urls(state)) | {
            str(x) for x in (state.get("text_links") or []) if isinstance(x, str)
        }
        updated_urls = set(_iter_button_urls(updated)) | {
            str(x) for x in (updated.get("text_links") or []) if isinstance(x, str)
        }

        for key, aliases in non_operator_aliases.items():
            if any(alias in source_urls for alias in aliases):
                assert link_overrides[key] in updated_urls
                replaced_urls += 1

    assert replaced_urls > 0


def test_support_ticket_and_wallet_help_aliases_are_detected() -> None:
    catalog = _catalog()

    assert "support_ticket" in catalog.link_url_aliases
    assert "wallet_help" in catalog.link_url_aliases
    assert "https://t.me/mask24_bot" in set(catalog.link_url_aliases["support_ticket"])
    assert "https://telegra.ph/CHto-takoe-koshelek-07-26" in set(catalog.link_url_aliases["wallet_help"])


def test_all_button_urls_are_mapped_to_admin_link_keys() -> None:
    catalog = _catalog()
    unmatched: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for state in catalog.states.values():
        for row in state.get("button_rows") or []:
            if not isinstance(row, list):
                continue
            for btn in row:
                if not isinstance(btn, dict):
                    continue
                text = str(btn.get("text") or "")
                url = str(btn.get("url") or "").strip()
                if not url:
                    continue
                key = (text, url)
                if key in seen:
                    continue
                seen.add(key)
                if not _match_link_key(text):
                    unmatched.append(key)

    assert unmatched == []
