import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
from aiogram.types import Message, User, PhotoSize, CallbackQuery

from app.runtime import FlowRuntime, UserSession
from app.catalog import FlowCatalog
from app.context import AppContext
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
