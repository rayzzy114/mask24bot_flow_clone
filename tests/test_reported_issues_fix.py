import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from aiogram.types import CallbackQuery, Message, User
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
async def test_start_forces_live_rate_refresh(runtime_ctx):
    runtime, catalog = runtime_ctx
    runtime.app_context.rates.get_rates = AsyncMock(return_value={"btc": 1.0, "usdt": 1.0})
    runtime._send_state_by_id = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")

    await runtime.start(msg)

    runtime.app_context.rates.get_rates.assert_awaited_once_with(force=True)
    runtime._send_state_by_id.assert_awaited_once()
    assert runtime._send_state_by_id.await_args.args[1] == catalog.start_state_id

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
    msg.answer = AsyncMock()
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


@pytest.mark.asyncio
async def test_cancel_from_regular_order_flow_moves_to_start(runtime_ctx):
    runtime, _ = runtime_ctx
    order_state = "9ff74b9bf7f060310f1e52607e00c4b7"
    start_state = runtime.catalog.start_state_id
    session = UserSession(state_id=order_state, history=[runtime.catalog.start_state_id, order_state])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "❌ Отмена"
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == start_state
    runtime._send_state_by_id.assert_awaited_with(msg, start_state, session=session)


@pytest.mark.asyncio
async def test_cancel_from_runtime_prequote_moves_to_start(runtime_ctx):
    runtime, _ = runtime_ctx
    start_state = runtime.catalog.start_state_id
    session = UserSession(
        state_id="__runtime_prequote__",
        history=[start_state, "__runtime_prequote__"],
        selected_coin="BTC",
        pending_requisites_state="9ff74b9bf7f060310f1e52607e00c4b7",
    )
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "❌ Отмена"
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == start_state
    assert session.pending_requisites_state == ""
    runtime._send_state_by_id.assert_awaited_with(msg, start_state, session=session)


@pytest.mark.asyncio
async def test_invalid_verification_card_shows_card_error_and_stays_put(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    verify_intro = "0766f67fa47dc42e73977f19493cc7a3"
    verify_card_input = "282f5bb08cb59ce7b0d5edcc89657467"

    session = UserSession(state_id=verify_card_input, history=[verify_offer, verify_intro, verify_card_input])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "1234"
    msg.photo = []
    msg.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == verify_card_input
    msg.answer.assert_awaited_once_with("⚠️ Введите корректный номер карты: 16 цифр, можно с пробелами или без.")
    runtime._send_state_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_amount_shows_amount_error_and_stays_put(runtime_ctx):
    runtime, _ = runtime_ctx
    amount_state_id = "4638c2dc946f913813ff1d81427e5703"
    session = UserSession(state_id=amount_state_id, history=[runtime.catalog.start_state_id, amount_state_id])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "abc"
    msg.photo = []
    msg.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == amount_state_id
    msg.answer.assert_awaited_once_with("⚠️ Введите корректную сумму числом.")
    runtime._send_state_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_verification_photo_state_rejects_text_without_photo(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_photo_state = "ec7347857d2b2531cf84d3d239457019"
    session = UserSession(state_id=verify_photo_state, history=[verify_photo_state])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "отправил"
    msg.photo = []
    msg.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == verify_photo_state
    msg.answer.assert_awaited_once_with("⚠️ На этом шаге нужно отправить именно фото карты с листком и паролем.")
    runtime._send_state_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_balance_send_shows_replenish_notice_and_stays_put(runtime_ctx):
    runtime, _ = runtime_ctx
    zero_balance_wallet_state = "4dd498fb2857472407baa8a4e213d9d9"
    session = UserSession(
        state_id=zero_balance_wallet_state,
        history=[runtime.catalog.start_state_id, zero_balance_wallet_state],
    )
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "📤 Отправить"
    msg.photo = []
    msg.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == zero_balance_wallet_state
    msg.answer.assert_awaited_once_with(
        "⚠️ Баланс нулевой. Отправить ничего нельзя.\n\nПополните баланс, чтобы отправить BTC."
    )
    runtime._send_state_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_zero_balance_send_callback_shows_replenish_notice_and_stays_put(runtime_ctx):
    runtime, _ = runtime_ctx
    zero_balance_wallet_state = "4dd498fb2857472407baa8a4e213d9d9"
    session = UserSession(
        state_id=zero_balance_wallet_state,
        history=[runtime.catalog.start_state_id, zero_balance_wallet_state],
    )
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime.tokens.token_to_action["send_zero_token"] = "📤 Отправить"

    callback_message = MagicMock(spec=Message)
    callback_message.answer = AsyncMock()

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = User(id=999, is_bot=False, first_name="Tester")
    cb.data = "send_zero_token"
    cb.message = callback_message
    cb.answer = AsyncMock()

    await runtime.on_callback(cb)

    assert session.state_id == zero_balance_wallet_state
    callback_message.answer.assert_awaited_once_with(
        "⚠️ Баланс нулевой. Отправить ничего нельзя.\n\nПополните баланс, чтобы отправить BTC."
    )
    runtime._send_state_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_verification_moves_to_exchange_picker_not_order_state(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_offer = "cb77e2d256ec6da86cc46a9c11857718"
    exchange_picker = "d18a3c1f0ab4ccc32fda8910cb2ce45c"
    bad_order_state = "38411b3ac84128632f281182e8a4f9db"

    session = UserSession(state_id=verify_offer, history=[runtime.catalog.start_state_id, verify_offer])
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "⏩ Пропустить"
    msg.photo = []

    await runtime.on_message(msg)

    assert session.state_id == exchange_picker
    assert session.state_id != bad_order_state
    runtime._send_state_by_id.assert_awaited_with(msg, exchange_picker, session=session)


@pytest.mark.asyncio
async def test_verification_success_photo_contains_start_exchange_button(runtime_ctx):
    runtime, _ = runtime_ctx
    runtime.media_dir.mkdir(parents=True, exist_ok=True)
    (runtime.media_dir / "verif.png").write_bytes(b"fake-png")

    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()

    await runtime._send_verification_success(msg)

    assert msg.answer_photo.await_args is not None
    reply_markup = msg.answer_photo.await_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    button_texts = [
        button.text
        for row in reply_markup.inline_keyboard
        for button in row
    ]
    assert "🔄 Начать обмен" in button_texts


@pytest.mark.asyncio
async def test_generic_invalid_input_uses_fallback_error(runtime_ctx, monkeypatch):
    runtime, catalog = runtime_ctx
    generic_state_id = "generic_invalid_input_state"
    catalog.states[generic_state_id] = {"text": "Введите значение"}

    session = UserSession(state_id=generic_state_id, history=[catalog.start_state_id, generic_state_id])
    runtime.sessions[999] = session

    original_accepts_input = catalog.state_accepts_input
    original_resolve_action = catalog.resolve_action

    monkeypatch.setattr(
        catalog,
        "state_accepts_input",
        lambda state_id: True if state_id == generic_state_id else original_accepts_input(state_id),
    )
    monkeypatch.setattr(
        catalog,
        "resolve_action",
        lambda state_id, action_text, is_text_input=False: None
        if state_id == generic_state_id
        else original_resolve_action(state_id, action_text, is_text_input=is_text_input),
    )

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = ""
    msg.caption = None
    msg.photo = []
    msg.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(msg)

    assert session.state_id == generic_state_id
    msg.answer.assert_awaited_once_with("⚠️ Введенные данные некорректны. Пожалуйста, проверьте формат и попробуйте снова.")
    runtime._send_state_by_id.assert_not_awaited()


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


@pytest.mark.parametrize(
    ("coin", "expected_target"),
    [
        ("BTC", "dd8e48ace94f57bf3eba334f6ab5b7d2"),
        ("LTC", "dd8e48ace94f57bf3eba334f6ab5b7d2"),
        ("USDT", "f29257f079d6fcdcb4dccc7ccf79bf53"),
        ("ETH", "dd8e48ace94f57bf3eba334f6ab5b7d2"),
    ],
)
def test_contextual_payment_route_for_supported_crypto_flows(runtime_ctx, coin, expected_target):
    runtime, _ = runtime_ctx
    payment_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    session = UserSession(state_id=payment_state, history=[payment_state], selected_coin=coin)

    target = runtime._resolve_contextual_transition(payment_state, "💳 Карты на карту", session)
    assert target == expected_target


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
async def test_usdt_card_to_card_opens_network_picker(runtime_ctx):
    runtime, _ = runtime_ctx
    method_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    network_picker = "f29257f079d6fcdcb4dccc7ccf79bf53"
    session = UserSession(state_id=method_state, history=[method_state], selected_coin="USDT")
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "💳 Карты на карту"

    await runtime.on_message(msg)

    assert session.state_id == network_picker
    runtime._send_state_by_id.assert_awaited_with(msg, network_picker, session=session)


@pytest.mark.asyncio
async def test_payment_method_picker_renders_methods_from_admin_settings(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    method_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    runtime.app_context.settings.data["requisites"]["payment_methods"] = ["СБП", "Наличные"]
    runtime._get_live_rates_rub = AsyncMock(return_value={})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")

    await runtime._send_state_by_id(msg, method_state, session=UserSession(state_id=method_state))

    sent_state = send_state_mock.await_args.args[1]
    rows = sent_state.get("button_rows") or []
    flat_texts = [str(btn.get("text") or "") for row in rows for btn in row if isinstance(btn, dict)]
    assert flat_texts == ["СБП", "Наличные", "🔙 Назад"]


@pytest.mark.asyncio
async def test_custom_payment_method_uses_payment_picker_flow(runtime_ctx):
    runtime, _ = runtime_ctx
    method_state = "230eb12bd9b1d8c5aea8da3109ab23ab"
    amount_state = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    runtime.app_context.settings.data["requisites"]["payment_methods"] = ["СБП", "Наличные"]
    session = UserSession(state_id=method_state, history=[method_state], selected_coin="BTC")
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "СБП"

    await runtime.on_message(msg)

    assert session.selected_payment_method == "СБП"
    assert session.state_id == amount_state
    runtime._send_state_by_id.assert_awaited_with(msg, amount_state, session=session)


@pytest.mark.asyncio
async def test_usdt_network_choice_is_remembered_for_trc20(runtime_ctx):
    runtime, _ = runtime_ctx
    network_picker = "f29257f079d6fcdcb4dccc7ccf79bf53"
    amount_state = "d10355801a11f2d98b2f14663355934e"
    session = UserSession(state_id=network_picker, history=[network_picker], selected_coin="USDT")
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "USDT (TRC20)"

    await runtime.on_message(msg)

    assert session.state_id == amount_state
    assert session.selected_network == "TRC20"


@pytest.mark.asyncio
async def test_usdt_wallet_state_uses_selected_trc20_quote(runtime_ctx):
    runtime, _ = runtime_ctx
    wallet_state = "7ce70c281eb57574028a6b6d3a63013b"
    trc20_quote = "4a985fd53a6cf0fb74877721f588a4d0"
    session = UserSession(
        state_id=wallet_state,
        history=[wallet_state],
        selected_coin="USDT",
        selected_network="TRC20",
    )
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "TR4D37Xfnr52cEPAtnvf5X9vVGFCqeiRX3"
    msg.caption = None
    msg.photo = []

    await runtime.on_message(msg)

    assert session.state_id == trc20_quote
    runtime._send_state_by_id.assert_awaited_with(msg, trc20_quote, session=session)


@pytest.mark.asyncio
async def test_usdt_wallet_state_uses_selected_bsc20_quote(runtime_ctx):
    runtime, _ = runtime_ctx
    wallet_state = "7ce70c281eb57574028a6b6d3a63013b"
    bsc20_quote = "d600074b23116f8c1024a7916d46d43e"
    session = UserSession(
        state_id=wallet_state,
        history=[wallet_state],
        selected_coin="USDT",
        selected_network="BSC20",
    )
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "0x2b90e061a517db2bbd7e39ef7f733fd234b494ca"
    msg.caption = None
    msg.photo = []

    await runtime.on_message(msg)

    assert session.state_id == bsc20_quote
    runtime._send_state_by_id.assert_awaited_with(msg, bsc20_quote, session=session)


@pytest.mark.asyncio
async def test_usdt_quote_state_uses_requested_amount_and_destination_wallet(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    quote_state = "d600074b23116f8c1024a7916d46d43e"
    wallet = "TBgHCowaEwfUe8UjYC34w3rcR9uQomzDY"
    session = UserSession(
        state_id=quote_state,
        history=[quote_state],
        selected_coin="USDT",
        selected_network="BSC20",
    )
    session.requested_coin_amount = 19.0
    session.destination_wallet = wallet

    runtime._get_live_rates_rub = AsyncMock(return_value={"USDT": 95.0})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")

    await runtime._send_state_by_id(msg, quote_state, session=session)

    sent_state = send_state_mock.await_args.args[1]
    text = str(sent_state.get("text") or "")
    text_html = str(sent_state.get("text_html") or "")
    assert "Получите: 19" in text
    assert "Получите:</strong> 19" in text_html
    assert wallet in text
    assert f"<code>{wallet}</code>" in text_html
    assert "(копируется)" in text
    assert "(копируется)" in text_html
    assert "Комиссия сервиса: 2.5%" in text
    assert "Комиссия сервиса: 2.5%" in text_html
    assert "Получите: 35" not in text
    assert "0x2b90e061a517db2bbd7e39ef7f733fd234b494ca" not in text


@pytest.mark.asyncio
async def test_requisites_code_block_does_not_include_braces(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    requisites_state = "c470c94e034f1631e0c841615c07c46b"
    runtime.app_context.settings.data["requisites"]["single_value"] = "2200 0000 0000 0000"
    runtime._get_live_rates_rub = AsyncMock(return_value={})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")

    await runtime._send_state_by_id(
        msg,
        requisites_state,
        session=UserSession(state_id=requisites_state, selected_payment_method="Перевод на карту"),
    )

    sent_state = send_state_mock.await_args.args[1]
    assert "{}" not in str(sent_state.get("text_html") or "")
    assert "<code" in str(sent_state.get("text_html") or "")
    assert "2200 0000 0000 0000" in str(sent_state.get("text_html") or "")


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
async def test_btc_amount_state_valid_input_bypasses_captured_max_error(runtime_ctx):
    runtime, _ = runtime_ctx
    amount_state = "dd8e48ace94f57bf3eba334f6ab5b7d2"
    wallet_state = "dfff19cf359e360e6644c920d8eb7c6b"
    session = UserSession(state_id=amount_state, history=[amount_state], selected_coin="BTC")
    runtime.sessions[999] = session
    runtime._coin_max_amount = AsyncMock(return_value=0.17994790)
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "0.0005"

    await runtime.on_message(msg)

    assert session.state_id == wallet_state
    runtime._send_state_by_id.assert_awaited_with(msg, wallet_state, session=session)


@pytest.mark.asyncio
async def test_xmr_amount_state_valid_input_bypasses_captured_max_error(runtime_ctx):
    runtime, _ = runtime_ctx
    amount_state = "c7dc1b492541b449585da857e71c7e29"
    wallet_state = "7d04573c2e8a4686265d3b1a265c97c5"
    session = UserSession(state_id=amount_state, history=[amount_state], selected_coin="XMR")
    runtime.sessions[999] = session
    runtime._coin_max_amount = AsyncMock(return_value=10.0)
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "0.5"

    await runtime.on_message(msg)

    assert session.state_id == wallet_state
    runtime._send_state_by_id.assert_awaited_with(msg, wallet_state, session=session)


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
    session = UserSession(state_id=wallet_state, history=[runtime.catalog.start_state_id, wallet_state], selected_coin="BTC")
    session.requested_coin_amount = 0.0005
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.text = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    msg.caption = None
    msg.photo = []

    await runtime.on_message(msg)

    assert session.state_id == "__runtime_prequote__"
    assert session.pending_requisites_state == runtime.catalog.resolve_system_next(wallet_state)
    runtime._send_state_by_id.assert_awaited_with(msg, "__runtime_prequote__", session=session)


@pytest.mark.asyncio
async def test_coin_max_amount_for_usdt_is_rub_budget(runtime_ctx):
    runtime, _ = runtime_ctx
    max_usdt = await runtime._coin_max_amount("USDT")
    # 1_000_000 RUB / 105 RUB per USDT (fallback) ≈ 9523.8 USDT
    assert max_usdt == pytest.approx(1_000_000.0 / 105.0, rel=0.001)


@pytest.mark.asyncio
async def test_coin_max_amount_for_eth_is_rub_budget(runtime_ctx):
    runtime, _ = runtime_ctx
    max_eth = await runtime._coin_max_amount("ETH")
    # 1_000_000 RUB / 180_000 RUB per ETH (fallback) ≈ 5.55 ETH
    assert max_eth == pytest.approx(1_000_000.0 / 180_000.0, rel=0.001)


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
    exchange_picker = "d18a3c1f0ab4ccc32fda8910cb2ce45c"
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
    # Step 1: acceptance message sent immediately (naproverk.jpg absent → plain answer)
    msg.answer.assert_awaited_once()
    accept_call_text = msg.answer.await_args.args[0]
    assert "Заявка на верификацию принята" in accept_call_text
    assert "2200 1234 5678 9012" in accept_call_text
    assert "рассмотрена в ближайшее время" in accept_call_text
    # Step 2: 15s delay then success photo
    sleep_mock.assert_awaited_once_with(15)
    msg.answer_photo.assert_awaited_once()
    assert msg.answer_photo.await_args is not None
    assert msg.answer_photo.await_args.kwargs.get("caption") == "✅ <b>Успешная верификация!</b>"
    assert msg.answer_photo.await_args.kwargs.get("parse_mode") == ParseMode.HTML
    assert session.state_id == exchange_picker


@pytest.mark.asyncio
async def test_after_successful_verification_exchange_message_is_not_blocked_by_photo_state(runtime_ctx):
    runtime, _ = runtime_ctx
    verify_photo_state = "ec7347857d2b2531cf84d3d239457019"
    exchange_entry = "cb77e2d256ec6da86cc46a9c11857718"
    session = UserSession(state_id=verify_photo_state, history=[verify_photo_state])
    runtime.sessions[999] = session

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()

    await runtime._send_verification_success(msg, session=session)

    assert session.state_id == "d18a3c1f0ab4ccc32fda8910cb2ce45c"

    followup = MagicMock(spec=Message)
    followup.from_user = User(id=999, is_bot=False, first_name="Tester")
    followup.text = "Обмен"
    followup.photo = []
    followup.answer = AsyncMock()
    runtime._send_state_by_id = AsyncMock()

    await runtime.on_message(followup)

    runtime._send_state_by_id.assert_awaited_with(followup, exchange_entry, session=session)
    followup.answer.assert_not_awaited()


def test_payment_proof_texts_use_bold_and_administrator_wording():
    assert "<b>" in PAYMENT_PROOF_PROMPT
    assert "администратору" in PAYMENT_PROOF_PROMPT.lower()
    assert "<b>" in PAYMENT_PROOF_SENT
    assert "администратору" in PAYMENT_PROOF_SENT.lower()


@pytest.mark.asyncio
async def test_quote_agree_shows_short_requisites_search_before_order_state(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    quote_state_id = "d600074b23116f8c1024a7916d46d43e"
    order_state_id = "c470c94e034f1631e0c841615c07c46b"
    session = UserSession(
        state_id=quote_state_id,
        history=[runtime.catalog.start_state_id, quote_state_id],
        selected_coin="USDT",
        selected_payment_method="Перевод на карту",
    )
    session.last_action_ts = 0.0
    runtime.sessions[999] = session
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()
    runtime._send_requisites_selection_notice = AsyncMock()
    runtime.tokens.token_to_action["agree_quote_token"] = "✅ Согласен"

    sleep_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.asyncio.sleep", sleep_mock)
    monkeypatch.setattr("app.runtime.random.randint", MagicMock(return_value=7))

    callback_message = MagicMock(spec=Message)
    callback_message.answer = AsyncMock()

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = User(id=999, is_bot=False, first_name="Tester")
    cb.data = "agree_quote_token"
    cb.message = callback_message
    cb.answer = AsyncMock()

    await runtime.on_callback(cb)

    runtime._send_requisites_selection_notice.assert_awaited_once_with(callback_message)
    sleep_mock.assert_awaited_once_with(7)
    runtime._send_state_by_id.assert_awaited_with(callback_message, order_state_id, session=session)


@pytest.mark.asyncio
async def test_quote_agree_acknowledges_callback_before_waiting_for_requisites(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    quote_state_id = "d600074b23116f8c1024a7916d46d43e"
    order_state_id = "c470c94e034f1631e0c841615c07c46b"
    session = UserSession(
        state_id=quote_state_id,
        history=[runtime.catalog.start_state_id, quote_state_id],
        selected_coin="USDT",
        selected_payment_method="Перевод на карту",
    )
    runtime.sessions[999] = session
    runtime.tokens.token_to_action["agree_quote_token_2"] = "✅ Согласен"

    events: list[str] = []

    async def notice_side_effect(*args, **kwargs):
        events.append("notice")

    async def sleep_side_effect(*args, **kwargs):
        events.append("sleep")

    async def send_state_side_effect(*args, **kwargs):
        events.append("send_state")

    runtime._send_requisites_selection_notice = AsyncMock(side_effect=notice_side_effect)
    runtime._send_state_by_id = AsyncMock(side_effect=send_state_side_effect)
    runtime._send_system_chain = AsyncMock()
    monkeypatch.setattr("app.runtime.asyncio.sleep", AsyncMock(side_effect=sleep_side_effect))
    monkeypatch.setattr("app.runtime.random.randint", MagicMock(return_value=7))

    callback_message = MagicMock(spec=Message)
    callback_message.answer = AsyncMock()

    async def cb_answer_side_effect(*args, **kwargs):
        events.append("cb_answer")

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = User(id=999, is_bot=False, first_name="Tester")
    cb.data = "agree_quote_token_2"
    cb.message = callback_message
    cb.answer = AsyncMock(side_effect=cb_answer_side_effect)

    await runtime.on_callback(cb)

    assert events == ["cb_answer", "notice", "sleep", "send_state"]
    runtime._send_state_by_id.assert_awaited_with(callback_message, order_state_id, session=session)


@pytest.mark.asyncio
async def test_quote_agree_deletes_requisites_notice_after_order_sent(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    quote_state_id = "d600074b23116f8c1024a7916d46d43e"
    order_state_id = "c470c94e034f1631e0c841615c07c46b"
    session = UserSession(
        state_id=quote_state_id,
        history=[runtime.catalog.start_state_id, quote_state_id],
        selected_coin="USDT",
        selected_payment_method="Перевод на карту",
    )
    runtime.sessions[999] = session
    runtime.tokens.token_to_action["agree_quote_token_3"] = "✅ Согласен"

    notice_message = MagicMock()
    notice_message.delete = AsyncMock()
    runtime._send_requisites_selection_notice = AsyncMock(return_value=notice_message)
    runtime._send_state_by_id = AsyncMock()
    runtime._send_system_chain = AsyncMock()
    monkeypatch.setattr("app.runtime.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("app.runtime.random.randint", MagicMock(return_value=7))

    callback_message = MagicMock(spec=Message)
    callback_message.answer = AsyncMock()

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = User(id=999, is_bot=False, first_name="Tester")
    cb.data = "agree_quote_token_3"
    cb.message = callback_message
    cb.answer = AsyncMock()

    await runtime.on_callback(cb)

    notice_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_requisites_search_notice_uses_requested_wording(runtime_ctx):
    runtime, _ = runtime_ctx
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock(side_effect=FileNotFoundError("no media"))

    await runtime._send_requisites_selection_notice(msg)

    sent = msg.answer.await_args.args[0]
    assert "Идет поиск реквизитов" in sent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coin", "wallet", "requested_amount", "rate"),
    [
        ("BTC", "bc1qga6mx70jx0uvfuk39eqpyyfwh9fsxzme75ckt7", 0.0005, 1_000_000.0),
        ("LTC", "LbyaWJcRTHV4wxJzNYVS1nMJriEi53PA66", 0.5, 10_000.0),
        ("ETH", "0x2b90e061a517db2bbd7e39ef7f733fd234b494ca", 0.5, 200_000.0),
        ("TRX", "TXSpvkp9idPHzoG9nd2CwSxc8Z5SskKZ8C", 100.0, 10.0),
        ("TON", "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", 10.0, 300.0),
    ],
)
async def test_btc_like_coins_use_runtime_prequote_before_requisites(runtime_ctx, coin, wallet, requested_amount, rate):
    runtime, _ = runtime_ctx
    session = UserSession(state_id="__runtime_prequote__", selected_coin=coin, selected_payment_method="Перевод на карту")
    session.requested_coin_amount = requested_amount
    session.destination_wallet = wallet

    state = runtime._build_runtime_prequote_state(session=session, live_rates_rub={coin: rate})

    text = str(state.get("text") or "")
    text_html = str(state.get("text_html") or "")
    assert "Комиссия сервиса: 2.5%" in text
    assert "(копируется)" in text
    assert wallet in text
    assert f"<code>{wallet}</code>" in text_html
    assert "(копируется)" in text_html


@pytest.mark.asyncio
async def test_order_state_skips_wait_notice_and_uses_copyable_payment_format(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    order_state_id = "9ff74b9bf7f060310f1e52607e00c4b7"
    session = UserSession(
        state_id=order_state_id,
        history=[runtime.catalog.start_state_id, order_state_id],
        selected_coin="BTC",
        selected_payment_method="Перевод на карту",
    )
    session.requested_coin_amount = 0.0005
    session.destination_wallet = "bc1qga6mx70jx0uvfuk39eqpyyfwh9fsxzme75ckt7"
    runtime.app_context.settings.data["requisites"]["single_value"] = "2200 0000 0000 0000"

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    runtime._send_requisites_selection_notice = AsyncMock()
    runtime._get_live_rates_rub = AsyncMock(return_value={"BTC": 1_000_000.0})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    await runtime._send_state_by_id(msg, order_state_id, session=session)

    runtime._send_requisites_selection_notice.assert_not_awaited()
    sent_state = send_state_mock.await_args.args[1]
    assert "Перевод на: VISA / MasterCard / MIR" in str(sent_state.get("text") or "")
    assert "Сумма к оплате: 512 RUB" in str(sent_state.get("text") or "")
    assert "Перевод BTC по адресу: bc1qga6mx70jx0uvfuk39eqpyyfwh9fsxzme75ckt7" in str(sent_state.get("text") or "")
    assert "<code>2200 0000 0000 0000</code>" in str(sent_state.get("text_html") or "")
    assert "<code>bc1qga6mx70jx0uvfuk39eqpyyfwh9fsxzme75ckt7</code>" in str(sent_state.get("text_html") or "")


@pytest.mark.asyncio
async def test_usdt_bsc_requisites_state_uses_same_runtime_order_format_as_btc(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    order_state_id = "25ae85dcf0be92c8142533c4fc45d102"
    session = UserSession(
        state_id=order_state_id,
        history=[runtime.catalog.start_state_id, order_state_id],
        selected_coin="USDT",
        selected_payment_method="Перевод на карту",
    )
    session.requested_coin_amount = 40.0
    session.destination_wallet = "0x66eb0a02ecf0089fb068cc2f73a3138a2ad9156a6"
    runtime.app_context.settings.data["requisites"]["single_value"] = "7777 7777 7777 7777"

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    runtime._get_live_rates_rub = AsyncMock(return_value={"USDT": 79.0})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    await runtime._send_state_by_id(msg, order_state_id, session=session)

    sent_state = send_state_mock.await_args.args[1]
    assert "Номер карты: 7777 7777 7777 7777" in str(sent_state.get("text") or "")
    assert "Перевод USDT по адресу: 0x66eb0a02ecf0089fb068cc2f73a3138a2ad9156a6" in str(sent_state.get("text") or "")
    assert "<code>7777 7777 7777 7777</code>" in str(sent_state.get("text_html") or "")
    assert "<code>0x66eb0a02ecf0089fb068cc2f73a3138a2ad9156a6</code>" in str(sent_state.get("text_html") or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coin", "wallet", "requested_amount", "rate", "expected_amount"),
    [
        ("BTC", "bc1qga6mx70jx0uvfuk39eqpyyfwh9fsxzme75ckt7", 0.0005, 1_000_000.0, 512),
        ("LTC", "LbyaWJcRTHV4wxJzNYVS1nMJriEi53PA66", 0.5, 10_000.0, 5125),
        ("USDT", "TR4D37Xfnr52cEPAtnvf5X9vVGFCqeiRX3", 35.0, 100.0, 3587),
        ("ETH", "0x2b90e061a517db2bbd7e39ef7f733fd234b494ca", 0.5, 200_000.0, 102500),
        ("XMR", "48CCnW8vhWf32Zw4aVnqezHdq9wSA4XeFF2tTdWfAqPSQq7uDwqxmvLGB1mLMMWEDj66cxvfz1R4ASJxpX94TN9qG5xBDeP", 0.5, 300_000.0, 153750),
        ("TRX", "TXSpvkp9idPHzoG9nd2CwSxc8Z5SskKZ8C", 100.0, 10.0, 1025),
        ("TON", "UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", 10.0, 300.0, 3075),
    ],
)
async def test_order_state_runtime_format_applies_to_all_supported_coins(
    runtime_ctx,
    monkeypatch,
    coin,
    wallet,
    requested_amount,
    rate,
    expected_amount,
):
    runtime, _ = runtime_ctx
    order_state_id = "9ff74b9bf7f060310f1e52607e00c4b7"
    session = UserSession(
        state_id=order_state_id,
        history=[runtime.catalog.start_state_id, order_state_id],
        selected_coin=coin,
        selected_payment_method="Перевод на карту",
    )
    session.requested_coin_amount = requested_amount
    session.destination_wallet = wallet
    runtime.app_context.settings.data["requisites"]["single_value"] = "2200 0000 0000 0000"

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")
    runtime._get_live_rates_rub = AsyncMock(return_value={coin: rate})
    send_state_mock = AsyncMock()
    monkeypatch.setattr("app.runtime.send_state", send_state_mock)

    await runtime._send_state_by_id(msg, order_state_id, session=session)

    sent_state = send_state_mock.await_args.args[1]
    assert f"Перевод {coin} по адресу: {wallet}" in str(sent_state.get("text") or "")
    assert f"<code>{wallet}</code>" in str(sent_state.get("text_html") or "")
    assert "<code>2200 0000 0000 0000</code>" in str(sent_state.get("text_html") or "")
    assert f"Сумма к оплате: {expected_amount} RUB" in str(sent_state.get("text") or "")


@pytest.mark.asyncio
async def test_send_state_stores_dynamic_rendered_context_in_session(runtime_ctx, monkeypatch):
    runtime, _ = runtime_ctx
    quote_state = "d600074b23116f8c1024a7916d46d43e"
    session = UserSession(
        state_id=quote_state,
        history=[quote_state],
        selected_coin="USDT",
        selected_network="BSC20",
    )
    session.requested_coin_amount = 19.0
    session.destination_wallet = "TBgHCowaEwfUe8UjYC34w3rcR9uQomzDY"
    runtime.sessions[999] = session
    runtime._get_live_rates_rub = AsyncMock(return_value={"USDT": 95.0})
    monkeypatch.setattr("app.runtime.send_state", AsyncMock())

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=999, is_bot=False, first_name="Tester")

    await runtime._send_state_by_id(msg, quote_state, session=session)

    assert "Получите: 19" in session.last_rendered_text
    assert "TBgHCowaEwfUe8UjYC34w3rcR9uQomzDY" in session.last_rendered_text
    assert "Получите: 35" not in session.last_rendered_text
