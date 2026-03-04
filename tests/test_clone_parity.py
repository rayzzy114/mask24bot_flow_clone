from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from app.constants import DEFAULT_LINKS
from app.context import AppContext
from app.catalog import FlowCatalog
from app.handlers.admin import build_admin_router
from app.rates import RateService
from app.runtime import outgoing_text_from_state
from app.storage import OrdersStore, SessionsStore, SettingsStore, UsersStore, MediaStore

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _catalog() -> FlowCatalog:
    return FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)


def test_text_exact_parity() -> None:
    catalog = _catalog()
    source_flow = _load_json(RAW_DIR / "flow.json")
    assert set(catalog.states.keys()) == set(source_flow.keys())
    for sid, state in source_flow.items():
        assert catalog.states[sid]["text"] == state["text"]


def test_formatting_exact_parity() -> None:
    catalog = _catalog()
    source_flow = _load_json(RAW_DIR / "flow.json")
    fields = [
        "text_html",
        "text_markdown",
        "entities",
        "entity_types",
        "entity_count",
        "has_formatting",
        "has_rich_formatting",
        "text_links",
    ]
    for sid, state in source_flow.items():
        for field in fields:
            assert catalog.states[sid].get(field) == state.get(field)


def test_button_rows_exact_parity() -> None:
    catalog = _catalog()
    source_flow = _load_json(RAW_DIR / "flow.json")
    for sid, state in source_flow.items():
        assert catalog.states[sid].get("button_rows") == state.get("button_rows")


def test_media_exact_parity() -> None:
    catalog = _catalog()
    source_flow = _load_json(RAW_DIR / "flow.json")
    for sid, state in source_flow.items():
        assert catalog.states[sid].get("media") == state.get("media")
        media = state.get("media")
        if isinstance(media, dict) and media.get("relpath"):
            media_path = MEDIA_DIR / Path(media["relpath"]).name
            assert media_path.exists()
        elif isinstance(media, str) and media.strip():
            media_path = MEDIA_DIR / Path(media).name
            assert media_path.exists()


def test_transition_exact_parity() -> None:
    catalog = _catalog()
    source_edges = _load_json(RAW_DIR / "edges.json")
    assert catalog.edges == source_edges


def test_no_generated_text() -> None:
    catalog = _catalog()
    captured_texts = {str(state.get("text") or "") for state in catalog.states.values()}
    for sid in catalog.states:
        outgoing = outgoing_text_from_state(catalog, sid)
        assert outgoing in captured_texts


def test_adminkit_mandatory(tmp_path: Path) -> None:
    settings = SettingsStore(
        path=tmp_path / "settings.json",
        default_commission=2.5,
        env_links=dict(DEFAULT_LINKS),
    )
    users = UsersStore(tmp_path / "users.json")
    orders = OrdersStore(tmp_path / "orders.json")
    rates = RateService(http_client=MagicMock(), ttl_seconds=1)
    sessions = SessionsStore(tmp_path / "sessions.json")
    media = MediaStore(tmp_path / "media_cache.json")
    ctx = AppContext(
        settings=settings,
        users=users,
        orders=orders,
        sessions=sessions,
        media=media,
        rates=rates,
        http_client=MagicMock(),
        admin_ids={101, 202},
        env_path=tmp_path / ".env",
    )
    router = build_admin_router(ctx)
    assert router.name == "admin"
