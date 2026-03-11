import pytest
from unittest.mock import MagicMock

from app.context import AppContext
from app.keyboards import kb_admin_panel
from app.storage import SettingsStore, UsersStore, OrdersStore, SessionsStore, MediaStore
from app.rates import RateService
from app.constants import DEFAULT_LINKS
from app.utils import parse_admin_ids

@pytest.fixture
def mock_ctx(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("ADMIN_IDS=123\nDEFAULT_COMMISSION_PERCENT=2.5")
    
    settings = SettingsStore(
        path=tmp_path / "settings.json",
        default_commission=2.5,
        env_links=dict(DEFAULT_LINKS)
    )
    users = UsersStore(tmp_path / "users.json")
    orders = OrdersStore(tmp_path / "orders.json")
    rates = RateService(http_client=MagicMock(), ttl_seconds=1)
    
    sessions = SessionsStore(tmp_path / "sessions.json")
    media = MediaStore(tmp_path / "media_cache.json")
    client = MagicMock()
    ctx = AppContext(
        settings=settings,
        users=users,
        orders=orders,
        sessions=sessions,
        media=media,
        rates=rates,
        http_client=client,
        admin_ids={123},
        env_path=env_path
    )
    return ctx

@pytest.mark.anyio
async def test_admin_commission_logic(mock_ctx):
    # Verify initial
    assert mock_ctx.settings.commission_percent == 2.5
    
    # Update through settings
    await mock_ctx.settings.set_commission(5.5)
    assert mock_ctx.settings.commission_percent == 5.5
    
    # Verify it saves to JSON
    new_settings = SettingsStore(mock_ctx.settings.path, 2.5, {})
    assert new_settings.commission_percent == 5.5

@pytest.mark.anyio
async def test_admin_requisites_logic(mock_ctx):
    # Toggle mode
    assert mock_ctx.settings.requisites_mode == "single"
    await mock_ctx.settings.toggle_requisites_mode()
    assert mock_ctx.settings.requisites_mode == "split"
    
    # Set split requisites
    method = mock_ctx.settings.payment_methods()[0]
    await mock_ctx.settings.set_method_requisites(method, "TestBank", "ReqValue")
    
    bank, val = mock_ctx.settings.method_requisites(method)
    assert bank == "TestBank"
    assert val == "ReqValue"
    
    # Check JSON persistence
    new_settings = SettingsStore(mock_ctx.settings.path, 2.5, {})
    bank2, val2 = new_settings.method_requisites(method)
    assert bank2 == "TestBank"
    assert val2 == "ReqValue"

@pytest.mark.anyio
async def test_payment_methods_logic(mock_ctx):
    # Add
    assert await mock_ctx.settings.add_payment_method("CryptoBank") is True
    assert "CryptoBank" in mock_ctx.settings.payment_methods()
    
    # Delete
    idx = mock_ctx.settings.payment_methods().index("CryptoBank")
    assert await mock_ctx.settings.delete_payment_method(idx) is True
    assert "CryptoBank" not in mock_ctx.settings.payment_methods()

@pytest.mark.anyio
async def test_sell_wallets_logic(mock_ctx):
    # Set sell wallet
    assert await mock_ctx.settings.set_sell_wallet("btc", "bc1qtest") is True
    assert mock_ctx.settings.sell_wallet("btc") == "bc1qtest"
    
    # Check persistence
    new_settings = SettingsStore(mock_ctx.settings.path, 2.5, {})
    assert new_settings.sell_wallet("btc") == "bc1qtest"


def test_admin_ids_parse_includes_requested_operator_id():
    admin_ids = parse_admin_ids("6131246501,8174646481")
    assert 8174646481 in admin_ids


def test_admin_panel_does_not_show_manual_refresh_rates_button():
    markup = kb_admin_panel(2.5)
    labels = [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]
    assert "🔄 Обновить курсы" not in labels


def test_admin_panel_shows_only_requested_link_buttons():
    markup = kb_admin_panel(2.5)
    labels = [
        button.text
        for row in markup.inline_keyboard
        for button in row
    ]
    for expected in ("FAQ", "Тикет", "Оператор"):
        assert expected in labels
    for removed in ("Канал", "Отзывы", "Отзыв-форма", "Условия", "Чат", "Менеджер"):
        assert removed not in labels
