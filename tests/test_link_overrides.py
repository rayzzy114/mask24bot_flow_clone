from __future__ import annotations

from pathlib import Path

from app.catalog import FlowCatalog
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
    non_operator_aliases = {
        key: aliases
        for key, aliases in catalog.link_url_aliases.items()
        if key != "operator" and aliases
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
