import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from aiogram.types import Message, User
from aiogram.enums import ParseMode

from app.runtime import FlowRuntime, UserSession
from app.catalog import FlowCatalog
from app.context import AppContext
from app.constants import PAYMENT_PROOF_PROMPT, PAYMENT_PROOF_SENT
from app.storage import SettingsStore, UsersStore, OrdersStore, SessionsStore, MediaStore
from app.rates import RateService
from app.constants import DEFAULT_LINKS

PROJECT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_DIR / "data" / "raw"
MEDIA_DIR = PROJECT_DIR / "data" / "media"

@pytest.fixture
def runtime_ctx(tmp_path):
    catalog = FlowCatalog.from_directory(raw_dir=RAW_DIR, media_dir=MEDIA_DIR)
    settings = SettingsStore(tmp_path / "settings.json", 2.5, dict(DEFAULT_LINKS))
    users = UsersStore(tmp_path / "users.json")
    orders = OrdersStore(tmp_path / "orders.json")
    sessions = SessionsStore(tmp_path / "sessions.json")
    media = MediaStore(tmp_path / "media_cache.json")
    rates = RateService(http_client=MagicMock(), ttl_seconds=1)
    ctx = AppContext(settings, users, orders, sessions, media, rates, http_client=MagicMock(), admin_ids={123}, env_path=tmp_path / ".env")
    runtime = FlowRuntime(project_dir=tmp_path, catalog=catalog, app_context=ctx)
    return runtime, catalog

@pytest.mark.asyncio
async def test_fix_back_button_after_global_jump(runtime_ctx):
    runtime, catalog = runtime_ctx
    
    # State A -> State B
    state_a = "state_a"
    state_b = "state_b"
    session = UserSession(state_id=state_b, history=[catalog.start_state_id, state_a, state_b])
    runtime.sessions[999] = session
    
    # Global jump
    global_text = next(iter(runtime.global_actions.keys()))
    global_target = runtime.global_actions[global_text]
    
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = global_text
    runtime._send_state_by_id = AsyncMock()
    
    await runtime.on_message(msg)
    assert session.state_id == global_target
    assert state_b in session.history
    
    # Back (reset anti-spam)
    session.last_action_ts = 0
    msg.text = "🔙 Назад"
    await runtime.on_message(msg)
    assert session.state_id == state_b

@pytest.mark.asyncio
async def test_fix_validation_for_invalid_amount(runtime_ctx):
    runtime, catalog = runtime_ctx
    
    # Find a state that looks for amount
    amount_state_id = "4638c2dc946f913813ff1d81427e5703" 
    if amount_state_id not in catalog.states:
        pytest.skip("BTC amount state not found")
        
    session = UserSession(state_id=amount_state_id, history=[catalog.start_state_id, amount_state_id])
    runtime.sessions[999] = session
    
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "хуй"
    runtime._send_state_by_id = AsyncMock()
    
    await runtime.on_message(msg)
    
    # Should stay in current state or go to error state, NOT move forward
    assert session.state_id == amount_state_id or "некорректный" in runtime._state_text(session.state_id).lower()

@pytest.mark.asyncio
async def test_fix_verification_flag_in_on_message(runtime_ctx):
    runtime, catalog = runtime_ctx
    
    session = UserSession(state_id=catalog.start_state_id, history=[catalog.start_state_id])
    runtime.sessions[999] = session
    
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "✅ Я оплатил"
    msg.answer = AsyncMock()
    
    await runtime.on_message(msg)
    assert session.awaiting_payment_proof is True

@pytest.mark.asyncio
async def test_fix_usdt_trc20_address_validation(runtime_ctx):
    runtime, catalog = runtime_ctx
    
    # State that mentions USDT and TRX/TRC20
    # Let's find one
    usdt_state_id = None
    for sid, state in catalog.states.items():
        t = state.get("text", "").upper()
        if "USDT" in t and ("TRX" in t or "TRC20" in t) and catalog.state_accepts_input(sid):
            usdt_state_id = sid
            break
            
    if not usdt_state_id:
        pytest.skip("USDT TRC20 state not found")
        
    session = UserSession(state_id=usdt_state_id, history=[catalog.start_state_id, usdt_state_id])
    runtime.sessions[999] = session
    
    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    
    # Invalid address (too short)
    msg.text = "T123"
    runtime._send_state_by_id = AsyncMock()
    await runtime.on_message(msg)
    assert session.state_id == usdt_state_id or "некорректный" in runtime._state_text(session.state_id).lower()
    
    # Valid TRX address
    msg.text = "TXSpvkp9idPHzoG9nd2CwSxc8Z5SskKZ8C"
    await runtime.on_message(msg)
    assert session.state_id != usdt_state_id
    assert "некорректный" not in runtime._state_text(session.state_id).lower()


@pytest.mark.asyncio
async def test_cancel_exits_verification_intro(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    verify_intro = "0766f67fa47dc42e73977f19493cc7a3"
    start_state = runtime.catalog.start_state_id

    session = UserSession(state_id=verify_intro, history=[verify_offer, verify_intro])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "❌ Отмена"
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == start_state
    runtime._send_state_by_id.assert_awaited()


@pytest.mark.asyncio
async def test_cancel_exits_verification_card_input(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    verify_intro = "0766f67fa47dc42e73977f19493cc7a3"
    verify_card_input = "282f5bb08cb59ce7b0d5edcc89657467"
    start_state = runtime.catalog.start_state_id

    session = UserSession(state_id=verify_card_input, history=[verify_offer, verify_intro, verify_card_input])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "❌ Отмена"
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == start_state
    runtime._send_state_by_id.assert_awaited()


def test_coin_button_fallback_uses_existing_coin_target(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "d18a3c1f0ab4ccc32fda8910cb2ce45c"
    expected = runtime.catalog.resolve_action(state_id, "Litecoin (LTC)")
    assert expected is not None
    assert runtime.catalog.resolve_action(state_id, "Bitcoin (BTC)") is None

    resolved = runtime._resolve_missing_coin_transition(state_id, "Bitcoin (BTC)")
    assert resolved == expected


def test_cancel_from_verification_resolver_exits_to_start(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    verify_intro = "0766f67fa47dc42e73977f19493cc7a3"
    session = UserSession(state_id=verify_intro, history=[verify_offer, verify_intro])

    prev_state = runtime._resolve_back_state(session, "❌ Отмена")
    assert prev_state == runtime.catalog.start_state_id


def test_cancel_variant_from_verification_exits_to_start(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    verify_intro = "0766f67fa47dc42e73977f19493cc7a3"
    session = UserSession(state_id=verify_intro, history=[verify_offer, verify_intro])

    prev_state = runtime._resolve_back_state(session, "❌ отмена")
    assert prev_state == runtime.catalog.start_state_id


def test_missing_button_maps_to_matching_system_next(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "39c2aa6a1534fa73a1a2ab96eef4cbb4"
    resolved = runtime._resolve_missing_action_transition(state_id, "💵 Мои вклады")
    assert resolved == "997a9afc7041753ec43fc07ab9ea3ddb"


def test_missing_button_maps_to_single_explicit_target(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "29f216fb23387d3f4a350cccb3c1a092"
    resolved = runtime._resolve_missing_action_transition(state_id, "🧾 Активные ваучеры")
    assert resolved == "31ed12ff3a0f71a3b2ac7a963bb23b6f"


def test_variant_back_label_is_detected(runtime_ctx):
    runtime, _ = runtime_ctx
    assert runtime._is_back_action("🔙 Нет промо, назад")


@pytest.mark.parametrize("coin", ["BTC", "LTC", "USDT", "ETH"])
def test_contextual_payment_route_for_all_crypto_coins_to_btc_flow(runtime_ctx, coin):
    runtime, _ = runtime_ctx
    payment_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    session = UserSession(state_id=payment_state, history=[payment_state], selected_coin=coin)

    target = runtime._resolve_contextual_transition(payment_state, "💳 Карты на карту", session)
    assert target == "dd8e48ace94f57bf3eba334f6ab5b7d2"


def test_contextual_payment_route_for_xmr_to_dedicated_state(runtime_ctx):
    """XMR has dedicated flow states, should NOT go through BTC-themed path."""
    runtime, _ = runtime_ctx
    payment_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    session = UserSession(state_id=payment_state, history=[payment_state], selected_coin="XMR")

    target = runtime._resolve_contextual_transition(payment_state, "💳 Карты на карту", session)
    assert target == "c7dc1b492541b449585da857e71c7e29"


def test_btc_amount_state_is_themed_for_ltc(runtime_ctx):
    runtime, _ = runtime_ctx
    base_state = dict(runtime.catalog.states["dd8e48ace94f57bf3eba334f6ab5b7d2"])
    session = UserSession(state_id="dd8e48ace94f57bf3eba334f6ab5b7d2", selected_coin="LTC")

    themed = runtime._apply_selected_coin_theming(
        base_state, state_id="dd8e48ace94f57bf3eba334f6ab5b7d2", session=session
    )
    assert "Litecoin (LTC)" in str(themed.get("text") or "")
    assert "Bitcoin (BTC)" not in str(themed.get("text") or "")


def test_btc_amount_state_is_themed_for_usdt(runtime_ctx):
    runtime, _ = runtime_ctx
    base_state = dict(runtime.catalog.states["dd8e48ace94f57bf3eba334f6ab5b7d2"])
    session = UserSession(state_id="dd8e48ace94f57bf3eba334f6ab5b7d2", selected_coin="USDT")

    themed = runtime._apply_selected_coin_theming(
        base_state, state_id="dd8e48ace94f57bf3eba334f6ab5b7d2", session=session
    )
    assert "USDT ($)" in str(themed.get("text") or "")


def test_btc_wallet_state_is_themed_for_ltc(runtime_ctx):
    runtime, _ = runtime_ctx
    base_state = dict(runtime.catalog.states["dfff19cf359e360e6644c920d8eb7c6b"])
    session = UserSession(state_id="dfff19cf359e360e6644c920d8eb7c6b", selected_coin="LTC")

    themed = runtime._apply_selected_coin_theming(
        base_state, state_id="dfff19cf359e360e6644c920d8eb7c6b", session=session
    )
    assert "Litecoin (LTC)" in str(themed.get("text") or "")
    assert str(themed.get("media") or "").endswith("coin_ltc_wallet.jpg")


def test_btc_max_amount_error_state_is_themed_for_ltc(runtime_ctx):
    runtime, _ = runtime_ctx
    base_state = dict(runtime.catalog.states["2fed3c394a37b41f55f21d474b5734ae"])
    session = UserSession(state_id="2fed3c394a37b41f55f21d474b5734ae", selected_coin="LTC")

    themed = runtime._apply_selected_coin_theming(
        base_state, state_id="2fed3c394a37b41f55f21d474b5734ae", session=session
    )
    assert "Litecoin (LTC)" in str(themed.get("text") or "")
    assert "Bitcoin (BTC)" not in str(themed.get("text") or "")


def test_coin_media_alias_path_for_eth_amount(runtime_ctx):
    runtime, _ = runtime_ctx
    relpath = runtime._coin_media_relpath(coin="ETH", role="amount")
    assert relpath == "media/coin_eth_amount.jpg"


@pytest.mark.parametrize("button_text", ["USDT (TRC20)", "USDT (BSC20)"])
def test_extract_coin_symbol_for_usdt_network_buttons(runtime_ctx, button_text):
    runtime, _ = runtime_ctx
    assert runtime._extract_coin_symbol(button_text) == "USDT"


def test_extract_coin_symbol_unknown_parenthesized_symbol_returns_empty(runtime_ctx):
    runtime, _ = runtime_ctx
    assert runtime._extract_coin_symbol("Foo (XYZ)") == ""


@pytest.mark.asyncio
async def test_max_amount_retry_moves_forward_when_amount_is_valid(runtime_ctx):
    runtime, _ = runtime_ctx
    max_error_state = "2fed3c394a37b41f55f21d474b5734ae"
    next_state = "dfff19cf359e360e6644c920d8eb7c6b"
    session = UserSession(state_id=max_error_state, history=[max_error_state], selected_coin="ETH")
    runtime.sessions[999] = session
    runtime._coin_max_amount = AsyncMock(return_value=1.0)
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "0.5"

    await runtime.on_message(msg)

    assert session.state_id == next_state
    runtime._send_state_by_id.assert_awaited_with(msg, next_state, session=session)


@pytest.mark.asyncio
async def test_max_amount_retry_resends_error_when_amount_too_large(runtime_ctx):
    runtime, _ = runtime_ctx
    max_error_state = "2fed3c394a37b41f55f21d474b5734ae"
    session = UserSession(state_id=max_error_state, history=[max_error_state], selected_coin="ETH")
    runtime.sessions[999] = session
    runtime._coin_max_amount = AsyncMock(return_value=1.0)
    runtime._send_state_by_id = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "2"

    await runtime.on_message(msg)

    assert session.state_id == max_error_state
    runtime._send_state_by_id.assert_awaited_with(msg, max_error_state, session=session)


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_replaces_btc_value_for_eth(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "2fed3c394a37b41f55f21d474b5734ae"
    base_state = dict(runtime.catalog.states[state_id])
    session = UserSession(state_id=state_id, selected_coin="ETH")
    runtime._coin_max_amount = AsyncMock(return_value=0.12345678)

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert "0.12345678" in str(themed.get("text") or "")
    assert "0.00190716" not in str(themed.get("text") or "")


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_replaces_value_in_text_html_strong_maximum(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "2fed3c394a37b41f55f21d474b5734ae"
    session = UserSession(state_id=state_id, selected_coin="ETH")
    runtime._coin_max_amount = AsyncMock(return_value=0.87654321)
    base_state = {
        "text": "Максимум 0.00190716 BTC",
        "text_html": "<strong>Максимум</strong> 0.00190716 BTC",
        "text_markdown": "*Максимум* 0.00190716 BTC",
    }

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert "0.87654321" in str(themed.get("text") or "")
    assert "0.87654321" in str(themed.get("text_html") or "")
    assert "0.87654321" in str(themed.get("text_markdown") or "")
    assert "0.00190716" not in str(themed.get("text") or "")
    assert "0.00190716" not in str(themed.get("text_html") or "")
    assert "0.00190716" not in str(themed.get("text_markdown") or "")


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_replaces_minimum_value(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    session = UserSession(state_id=state_id, selected_coin="ETH")
    runtime._coin_min_amount = AsyncMock(return_value=0.01234567)
    base_state = {
        "text": "💰Введи нужную сумму\nМинимум: 35.00 USDT",
        "text_html": "<code>Минимум: 35.00 USDT</code>",
        "text_markdown": "`Минимум: 35.00 USDT`",
    }

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert "0.01234567" in str(themed.get("text") or "")
    assert "0.01234567" in str(themed.get("text_html") or "")
    assert "35.00" not in str(themed.get("text") or "")


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_formats_usdt_minimum_with_two_decimals(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    session = UserSession(state_id=state_id, selected_coin="USDT")
    runtime._coin_min_amount = AsyncMock(return_value=35.5)
    base_state = {
        "text": "💰Введи нужную сумму в USDT ($)\nМинимум: 35.00 USDT",
        "text_html": "<code>Минимум: 35.00 USDT</code>",
        "text_markdown": "`Минимум: 35.00 USDT`",
    }

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert "35.50 USDT" in str(themed.get("text") or "")
    assert "35.50 USDT" in str(themed.get("text_html") or "")
    assert "35.50000000" not in str(themed.get("text") or "")
    assert "35.50000000" not in str(themed.get("text_html") or "")


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_formats_usdt_maximum_with_two_decimals(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "2fed3c394a37b41f55f21d474b5734ae"
    session = UserSession(state_id=state_id, selected_coin="USDT")
    runtime._coin_max_amount = AsyncMock(return_value=100.0)
    base_state = {
        "text": "⛔️ Максимум 0.00190716 Bitcoin (BTC), введите еще раз...",
        "text_html": "⛔️ <strong>Максимум</strong> 0.00190716 Bitcoin (BTC), введите еще раз...",
        "text_markdown": "⛔️ **Максимум** 0.00190716 Bitcoin (BTC), введите еще раз...",
    }

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert "100.00" in str(themed.get("text") or "")
    assert "100.00" in str(themed.get("text_html") or "")
    assert "100.00000000" not in str(themed.get("text") or "")
    assert "100.00000000" not in str(themed.get("text_html") or "")


def test_validate_input_rejects_eth_dust_with_selected_coin(runtime_ctx):
    runtime, _ = runtime_ctx
    amount_state_id = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    session = UserSession(state_id=amount_state_id, selected_coin="ETH")
    assert runtime._validate_input(amount_state_id, "0.00000001", session=session) is False
    assert runtime._validate_input(amount_state_id, "0.01000000", session=session) is True


@pytest.mark.asyncio
async def test_wallet_state_free_text_moves_to_system_next(runtime_ctx):
    runtime, _ = runtime_ctx
    wallet_state = "dfff19cf359e360e6644c920d8eb7c6b"
    expected_next = runtime.catalog.resolve_system_next(wallet_state)
    assert expected_next is not None

    session = UserSession(state_id=wallet_state, history=[runtime.catalog.start_state_id, wallet_state], selected_coin="BTC")
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    msg.caption = None
    msg.photo = []

    await runtime.on_message(msg)

    assert session.state_id == expected_next
    runtime._send_state_by_id.assert_awaited_with(msg, expected_next, session=session)


@pytest.mark.asyncio
async def test_coin_max_amount_for_usdt_is_not_btc_constant(runtime_ctx):
    runtime, _ = runtime_ctx
    max_usdt = await runtime._coin_max_amount("USDT")
    assert max_usdt == pytest.approx(100.0, rel=0.001)


@pytest.mark.asyncio
async def test_coin_max_amount_for_eth_is_not_btc_constant(runtime_ctx):
    runtime, _ = runtime_ctx
    max_eth = await runtime._coin_max_amount("ETH")
    assert 0 < max_eth < 1.0


@pytest.mark.asyncio
async def test_coin_min_amount_for_non_btc_returns_none_when_rates_missing(runtime_ctx):
    runtime, _ = runtime_ctx
    runtime._get_live_rates_rub = AsyncMock(return_value={"BTC": 0.0, "ETH": 0.0})
    assert await runtime._coin_min_amount("ETH") is None


@pytest.mark.asyncio
async def test_apply_dynamic_amount_limits_keeps_static_minimum_when_dynamic_unavailable(runtime_ctx):
    runtime, _ = runtime_ctx
    state_id = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    session = UserSession(state_id=state_id, selected_coin="ETH")
    runtime._coin_min_amount = AsyncMock(return_value=None)
    base_state = {"text": "Минимум: 35.00 USDT"}

    themed = await runtime._apply_dynamic_amount_limits(base_state, state_id=state_id, session=session)

    assert str(themed.get("text") or "") == "Минимум: 35.00 USDT"


@pytest.mark.asyncio
async def test_verification_photo_sends_delayed_success_media(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    verify_photo_state = "ec7347857d2b2531cf84d3d239457019"
    session = UserSession(state_id=verify_photo_state, history=[verify_photo_state])
    runtime.sessions[999] = session
    runtime.media_dir.mkdir(parents=True, exist_ok=True)
    (runtime.media_dir / "verif.png").write_bytes(b"fake-png")

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.photo = [MagicMock(file_id="photo_1")]
    msg.text = None
    msg.caption = None
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()

    runtime._forward_general_photo = AsyncMock()
    sleep_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.asyncio.sleep", sleep_mock)

    await runtime.on_message(msg)

    runtime._forward_general_photo.assert_not_awaited()
    msg.answer.assert_awaited_once_with(
        "⏳ <b>Подождите, мы вас верифицируем...</b>",
        parse_mode=ParseMode.HTML,
    )
    sleep_mock.assert_awaited_once_with(15)
    msg.answer_photo.assert_awaited_once()
    assert msg.answer_photo.await_args is not None
    assert msg.answer_photo.await_args.kwargs.get("caption") == "✅ <b>Успешная верификация!</b>"
    assert msg.answer_photo.await_args.kwargs.get("parse_mode") == ParseMode.HTML


def test_payment_proof_texts_use_bold_and_administrator_wording():
    assert "<b>" in PAYMENT_PROOF_PROMPT
    assert "администратору" in PAYMENT_PROOF_PROMPT.lower()
    assert "<b>" in PAYMENT_PROOF_SENT
    assert "администратору" in PAYMENT_PROOF_SENT.lower()


@pytest.mark.asyncio
async def test_order_state_sends_requisites_wait_notice(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    order_state_id = "9ff74b9bf7f060310f1e52607e00c4b7"
    session = UserSession(state_id=order_state_id, history=[runtime.catalog.start_state_id, order_state_id])

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    runtime._send_requisites_selection_notice = AsyncMock()
    sleep_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.asyncio.sleep", sleep_mock)
    monkeypatch.setattr("app.runtime.send_state", AsyncMock())

    await runtime._send_state_by_id(msg, order_state_id, session=session)

    runtime._send_requisites_selection_notice.assert_awaited_once_with(msg)
    sleep_mock.assert_awaited_once_with(15)
