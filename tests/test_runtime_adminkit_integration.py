from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.catalog import FlowCatalog
from app.constants import DEFAULT_LINKS
from app.context import AppContext
from app.rates import RateService
from app.runtime import FlowRuntime, UserSession
from app.storage import OrdersStore, SessionsStore, SettingsStore, UsersStore, MediaStore

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"


def _runtime(tmp_path: Path) -> tuple[FlowRuntime, AppContext, FlowCatalog]:
    catalog = FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)
    settings = SettingsStore(
        path=tmp_path / "settings.json",
        default_commission=2.5,
        env_links=dict(DEFAULT_LINKS),
    )
    users = UsersStore(tmp_path / "users.json")
    orders = OrdersStore(tmp_path / "orders.json")
    sessions = SessionsStore(tmp_path / "sessions.json")
    media = MediaStore(tmp_path / "media_cache.json")
    rates = RateService(http_client=MagicMock(), ttl_seconds=1)
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
    runtime = FlowRuntime(project_dir=tmp_path, catalog=catalog, app_context=ctx)
    return runtime, ctx, catalog


@pytest.mark.asyncio
async def test_payment_proof_creates_paid_order(tmp_path: Path) -> None:
    runtime, ctx, catalog = _runtime(tmp_path)
    method = ctx.settings.payment_methods()[0]

    session = UserSession(
        state_id=catalog.start_state_id,
        payment_context=(
            "Сумма: 123 456 RUB\n"
            "Крипта: 0.0123 BTC\n"
            "Кошелек: bc1qexamplewallet"
        ),
        selected_payment_method=method,
    )

    created = await runtime._create_paid_order(user_id=999, username="tester", session=session)
    stored = ctx.orders.get_order(str(created["order_id"]))

    assert stored is not None
    assert stored["status"] == "paid"
    assert stored["user_id"] == 999
    assert stored["coin_symbol"] == "BTC"
    assert stored["payment_method"] == method


@pytest.mark.asyncio
async def test_split_requisites_follow_selected_method(tmp_path: Path) -> None:
    runtime, ctx, catalog = _runtime(tmp_path)
    method = ctx.settings.payment_methods()[0]

    await ctx.settings.set_requisites_mode("split")
    assert await ctx.settings.set_method_requisites(method, "T-Bank", "5555 6666 7777 8888")

    session = UserSession(
        state_id=catalog.start_state_id,
        selected_payment_method=method,
    )

    value = runtime._effective_requisites_for_state(session, catalog.start_state_id)
    assert value == "5555 6666 7777 8888"
