from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.catalog import FlowCatalog
from app.constants import DEFAULT_LINKS
from app.context import AppContext
from app.rates import RateService
from app.runtime import FlowRuntime, UserSession
from app.overrides import RuntimeOverrides, apply_state_overrides
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
async def test_create_paid_order_uses_pending_order_id_and_dynamic_payment_context(tmp_path: Path) -> None:
    runtime, ctx, catalog = _runtime(tmp_path)
    method = ctx.settings.payment_methods()[0]

    session = UserSession(
        state_id="9ff74b9bf7f060310f1e52607e00c4b7",
        selected_payment_method=method,
        selected_coin="USDT",
        pending_order_id="654321",
    )
    session.payment_context = (
        "🗳 Заявка: №654321\n\n"
        "Перевод на: VISA / MasterCard / MIR\n"
        "Номер карты: 2200 0000 0000 0000\n"
        "Сумма к оплате: 3587 RUB\n\n"
        "Перевод USDT по адресу: TBgHCowaEwfUe8UjYC34w3rcR9uQomzDY"
    )

    created = await runtime._create_paid_order(user_id=999, username="tester", session=session)
    stored = ctx.orders.get_order("654321")

    assert created["order_id"] == "654321"
    assert stored is not None
    assert stored["wallet"] == "TBgHCowaEwfUe8UjYC34w3rcR9uQomzDY"
    assert stored["amount_rub"] == 3587.0
    assert stored["coin_symbol"] == "USDT"


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


@pytest.mark.asyncio
async def test_single_mode_requisites_updates_initialize_split_map(tmp_path: Path) -> None:
    settings = SettingsStore(
        path=tmp_path / "settings.json",
        default_commission=2.5,
        env_links=dict(DEFAULT_LINKS),
    )

    await settings.set_requisites_value("9999 8888 7777 6666")

    assert settings.requisites_mode == "single"
    assert settings.requisites_value == "9999 8888 7777 6666"
    for method in settings.payment_methods():
        bank, value = settings.method_requisites(method)
        assert bank == settings.requisites_bank
        assert value == "9999 8888 7777 6666"


def test_single_mode_requisites_are_applied_consistently_to_all_order_states(tmp_path: Path) -> None:
    runtime, ctx, catalog = _runtime(tmp_path)
    session = UserSession(state_id=catalog.start_state_id, selected_payment_method="Перевод на карту")
    target_requisites = "9999 8888 7777 6666"
    runtime.app_context.settings.data["requisites"]["single_value"] = target_requisites
    runtime.app_context.settings.data["requisites"]["split_by_method"] = {
        method: {"bank": runtime.app_context.settings.requisites_bank, "value": target_requisites}
        for method in runtime.app_context.settings.payment_methods()
    }

    for state_id in ("9ff74b9bf7f060310f1e52607e00c4b7", "38411b3ac84128632f281182e8a4f9db"):
        patched = apply_state_overrides(
            state=catalog.states[state_id],
            overrides=RuntimeOverrides(
                operator_url=ctx.settings.link("operator"),
                payment_requisites=runtime._effective_requisites_for_state(session, state_id),
                link_overrides=ctx.settings.all_links(),
                sell_wallet_overrides=ctx.settings.all_sell_wallets(),
                commission_percent=ctx.settings.commission_percent,
            ),
            operator_url_aliases=catalog.operator_url_aliases,
            operator_handle_aliases=catalog.operator_handle_aliases,
            detected_requisites=catalog.detected_requisites,
            link_url_aliases=catalog.link_url_aliases,
            sell_wallet_aliases=catalog.sell_wallet_aliases,
            live_rates_rub={},
        )

        assert f"Номер карты: {target_requisites}" in str(patched.get("text") or "")


@pytest.mark.asyncio
async def test_send_state_by_id_applies_admin_overrides_in_runtime_pipeline(tmp_path: Path, monkeypatch) -> None:
    runtime, ctx, catalog = _runtime(tmp_path)

    await ctx.settings.set_link("operator", "https://t.me/runtime_operator_777")
    await ctx.settings.set_link("terms", "https://example.com/runtime-terms")
    await ctx.settings.set_sell_wallet("ltc", "LTCRuntimeWallet111111111111111111111111111")
    await ctx.settings.set_requisites_value("1111 2222 3333 4444")

    runtime._get_live_rates_rub = AsyncMock(return_value={})
    runtime._send_requisites_selection_notice = AsyncMock()
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)
    monkeypatch.setattr("app.runtime.asyncio.sleep", AsyncMock())

    def _find_state_id(predicate) -> str:
        for sid, state in catalog.states.items():
            if predicate(state):
                return sid
        raise AssertionError("Expected matching state in captured flow")

    terms_aliases = set(catalog.link_url_aliases.get("terms", ()))
    assert terms_aliases

    terms_state_id = _find_state_id(
        lambda state: any(
            str(link) in terms_aliases
            for link in (state.get("text_links") or [])
            if isinstance(link, str)
        )
        or any(
            str(btn.get("url") or "") in terms_aliases
            for row in (state.get("button_rows") or [])
            if isinstance(row, list)
            for btn in row
            if isinstance(btn, dict)
        )
    )
    ltc_wallet_state_id = _find_state_id(
        lambda state: any(
            alias in "\n".join(
                [
                    str(state.get("text") or ""),
                    str(state.get("text_html") or ""),
                    str(state.get("text_markdown") or ""),
                ]
            )
            for alias in catalog.sell_wallet_aliases.get("ltc", ())
        )
    )

    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()

    await runtime._send_state_by_id(
        msg,
        "9ff74b9bf7f060310f1e52607e00c4b7",
        session=UserSession(state_id="9ff74b9bf7f060310f1e52607e00c4b7", selected_payment_method="Перевод на карту"),
    )
    requisites_state = send_state_mock.await_args_list[-1].args[1]
    assert "Номер карты: 1111 2222 3333 4444" in str(requisites_state.get("text") or "")

    await runtime._send_state_by_id(
        msg,
        terms_state_id,
        session=UserSession(state_id=terms_state_id),
    )
    terms_state = send_state_mock.await_args_list[-1].args[1]
    terms_urls = {
        str(link)
        for link in (terms_state.get("text_links") or [])
        if isinstance(link, str)
    }
    for row in terms_state.get("button_rows") or []:
        if isinstance(row, list):
            for btn in row:
                if isinstance(btn, dict) and btn.get("url"):
                    terms_urls.add(str(btn["url"]))
    assert "https://example.com/runtime-terms" in terms_urls

    await runtime._send_state_by_id(
        msg,
        ltc_wallet_state_id,
        session=UserSession(state_id=ltc_wallet_state_id),
    )
    wallet_state = send_state_mock.await_args_list[-1].args[1]
    wallet_blob = "\n".join(
        [
            str(wallet_state.get("text") or ""),
            str(wallet_state.get("text_html") or ""),
            str(wallet_state.get("text_markdown") or ""),
        ]
    )
    assert "LTCRuntimeWallet111111111111111111111111111" in wallet_blob
