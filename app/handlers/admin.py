import re
from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from dotenv import dotenv_values, load_dotenv, set_key

from ..constants import COINS, DEFAULT_LINKS, LINK_LABELS, SELL_WALLET_LABELS
from ..context import AppContext
from ..keyboards import (
    kb_admin_delete_payment_method_with_status,
    kb_admin_panel,
    kb_admin_requisites,
    kb_admin_sell_wallets,
    kb_admin_split_methods_pick,
    links_help_lines,
)
from ..states import AdminState
from ..telegram_helpers import callback_message, callback_user_id, message_user_id
from ..utils import (
    fmt_coin,
    fmt_money,
    parse_admin_ids,
    parse_non_negative_amount,
    safe_username,
)


def build_admin_router(ctx: AppContext) -> Router:
    router = Router(name="admin")

    def mode_label() -> str:
        return "Раздельные по способам оплаты" if ctx.settings.requisites_mode == "split" else "Единые"

    def methods_configured_map() -> dict[str, bool]:
        methods = ctx.settings.payment_methods()
        if ctx.settings.requisites_mode == "single":
            ok = bool(ctx.settings.requisites_bank.strip() and ctx.settings.requisites_value.strip())
            return {method: ok for method in methods}
        result: dict[str, bool] = {}
        for method in methods:
            bank, value = ctx.settings.method_requisites(method)
            result[method] = bool(bank.strip() and value.strip())
        return result

    def mask_value(key: str, value: str) -> str:
        if key == "BOT_TOKEN":
            if len(value) <= 12:
                return "***"
            return f"{value[:10]}...{value[-6:]}"
        return value

    async def apply_runtime_from_env() -> None:
        env = dotenv_values(ctx.env_path)
        ctx.admin_ids = parse_admin_ids((env.get("ADMIN_IDS") or "").strip())

        commission = parse_non_negative_amount(str(env.get("DEFAULT_COMMISSION_PERCENT") or ""))
        if commission is not None and 0 <= commission <= 50:
            await ctx.settings.set_commission(commission)

        for link_key in DEFAULT_LINKS:
            env_key = f"{link_key.upper()}_LINK"
            value = (env.get(env_key) or "").strip()
            if value:
                await ctx.settings.set_link(link_key, value)

    async def show_panel(message: Message) -> None:
        env = dotenv_values(ctx.env_path)
        env_lines = [
            f"<code>{key}</code> = <code>{mask_value(key, str(value))}</code>"
            for key, value in sorted(env.items())
        ]
        wallets = ctx.settings.all_sell_wallets()
        wallet_lines = "\n".join(
            f"{SELL_WALLET_LABELS[key]}: <code>{wallets.get(key, '').strip() or '-'}</code>"
            for key in SELL_WALLET_LABELS
        )
        text = (
            "<b>Админка</b>\n"
            f"Комиссия: <b>{ctx.settings.commission_percent:.2f}%</b>\n"
            f"Админов: <b>{len(ctx.admin_ids)}</b>\n\n"
            "<b>Ссылки:</b>\n"
            f"{links_help_lines(ctx.settings.all_links())}\n\n"
            "<b>Кошельки продажи:</b>\n"
            f"{wallet_lines}\n\n"
            "<b>.env переменные:</b>\n"
            f"{chr(10).join(env_lines) if env_lines else '<i>.env пуст</i>'}"
        )
        await message.answer(text, reply_markup=kb_admin_panel(ctx.settings.commission_percent))

    async def show_sell_wallets_panel(message: Message) -> None:
        wallets = ctx.settings.all_sell_wallets()
        lines = [
            f"• {SELL_WALLET_LABELS[key]}: <code>{wallets.get(key, '').strip() or '-'}</code>"
            for key in SELL_WALLET_LABELS
        ]
        text = "🪙 <b>Кошельки продажи крипты</b>\n\n" + "\n".join(lines)
        await message.answer(text, reply_markup=kb_admin_sell_wallets(wallets))

    async def show_requisites_panel(message: Message) -> None:
        rates = await ctx.rates.get_rates()
        methods = ctx.settings.payment_methods()
        configured = methods_configured_map()
        methods_lines = (
            "\n".join(
                f"• {'✅' if configured.get(item, False) else '❌'} {item}"
                for item in methods
            )
            if methods
            else "<i>Нет</i>"
        )
        split_lines = ""
        if ctx.settings.requisites_mode == "split":
            split_map = ctx.settings.split_method_map()
            split_lines = "\n".join(
                f"• {'✅' if configured.get(method, False) else '❌'} <b>{method}</b>: {row['bank']} / <code>{row['value']}</code>"
                for method, row in split_map.items()
            )
            split_lines = f"\n\n<b>Реквизиты по методам:</b>\n{split_lines}"

        text = (
            "💳 <b>Управление реквизитами</b>\n\n"
            f"Режим: <b>{mode_label()}</b>\n"
            f"Единый банк: <b>{ctx.settings.requisites_bank}</b>\n"
            f"Единые реквизиты: <code>{ctx.settings.requisites_value}</code>\n"
            f"Курс BTC: <b>{fmt_money(rates.get('btc', 0.0))}</b> RUB\n"
            f"Комиссия: <b>{ctx.settings.commission_percent:.2f}%</b>\n\n"
            f"<b>Способы оплаты:</b>\n{methods_lines}"
            f"{split_lines}"
        )
        await message.answer(
            text,
            reply_markup=kb_admin_requisites(
                commission_percent=ctx.settings.commission_percent,
                mode_label=mode_label(),
            ),
        )

    async def persist_env_value(key: str, value: str) -> None:
        set_key(
            dotenv_path=str(ctx.env_path),
            key_to_set=key,
            value_to_set=value,
            quote_mode="never",
        )
        load_dotenv(dotenv_path=ctx.env_path, override=True)
        await apply_runtime_from_env()

    @router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        await state.clear()
        await show_panel(message)

    @router.callback_query(F.data == "admin:req:back")
    async def admin_back(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await show_panel(msg)

    @router.callback_query(F.data == "admin:set_commission")
    @router.callback_query(F.data == "admin:req:commission")
    async def admin_set_commission(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        source: Literal["panel", "requisites"] = (
            "requisites" if callback.data == "admin:req:commission" else "panel"
        )
        await state.set_state(AdminState.waiting_admin_commission)
        await state.update_data(admin_return_to=source)
        await msg.answer("Отправьте новую комиссию в %, например: 2.5")

    @router.message(AdminState.waiting_admin_commission)
    async def admin_commission_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        value = parse_non_negative_amount(message.text or "")
        if value is None or value < 0 or value > 50:
            await message.answer("Введите корректную комиссию в диапазоне 0..50")
            return
        await persist_env_value("DEFAULT_COMMISSION_PERCENT", str(value))
        data = await state.get_data()
        await state.clear()
        if data.get("admin_return_to") == "requisites":
            await show_requisites_panel(message)
        else:
            await show_panel(message)

    @router.callback_query(F.data == "admin:set_env")
    async def admin_set_env(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.set_state(AdminState.waiting_admin_env)
        await msg.answer("Отправьте переменную в формате:\n<code>KEY=VALUE</code>")

    @router.message(AdminState.waiting_admin_env)
    async def admin_env_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        raw = (message.text or "").strip()
        if "=" not in raw:
            await message.answer("Неверный формат. Нужен <code>KEY=VALUE</code>")
            return
        key, value = raw.split("=", 1)
        key = key.strip().upper()
        value = value.strip()
        if not re.match(r"^[A-Z0-9_]+$", key):
            await message.answer("Неверный ключ. Разрешены A-Z, 0-9 и _.")
            return
        await persist_env_value(key, value)
        await state.clear()
        await message.answer(f"Обновлено: <code>{key}</code> = <code>{mask_value(key, value)}</code>")
        await show_panel(message)

    @router.callback_query(F.data.startswith("admin:set_link:"))
    async def admin_set_link(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        key = (callback.data or "").split(":")[-1]
        if key not in LINK_LABELS:
            await callback.answer("Неизвестный ключ", show_alert=True)
            return
        await callback.answer()
        await state.set_state(AdminState.waiting_admin_link)
        await state.update_data(link_key=key)
        await msg.answer(
            f"Отправьте новую ссылку для «{LINK_LABELS[key]}».\n"
            "Поддерживается https://... или t.me/..."
        )

    @router.message(AdminState.waiting_admin_link)
    async def admin_link_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        data = await state.get_data()
        key = data.get("link_key")
        if key not in LINK_LABELS:
            await state.clear()
            await message.answer("Сессия обновления ссылки сброшена.")
            return
        value = (message.text or "").strip()
        if value.startswith("t.me/"):
            value = "https://" + value
        if not re.match(r"^https?://", value):
            await message.answer("Нужна ссылка формата https://... или t.me/...")
            return
        await persist_env_value(f"{key.upper()}_LINK", value)
        await state.clear()
        await message.answer(f"Ссылка «{LINK_LABELS[key]}» обновлена.")
        await show_panel(message)

    @router.callback_query(F.data == "admin:requisites")
    async def admin_requisites(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await show_requisites_panel(msg)

    @router.callback_query(F.data == "admin:sell_wallets")
    async def admin_sell_wallets(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await show_sell_wallets_panel(msg)

    @router.callback_query(F.data == "admin:sell_wallets:back")
    async def admin_sell_wallets_back(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await show_panel(msg)

    @router.callback_query(F.data.startswith("admin:sell_wallet:set:"))
    async def admin_sell_wallet_set(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        key = (callback.data or "").split(":")[-1].strip().lower()
        if key not in SELL_WALLET_LABELS:
            await callback.answer("Неизвестный ключ", show_alert=True)
            return
        current = ctx.settings.sell_wallet(key)
        await callback.answer()
        await state.set_state(AdminState.waiting_admin_sell_wallet)
        await state.update_data(sell_wallet_key=key)
        await msg.answer(
            f"Отправьте новый кошелек для <b>{SELL_WALLET_LABELS[key]}</b>.\n"
            f"Текущее значение: <code>{current or '-'}</code>"
        )

    @router.message(AdminState.waiting_admin_sell_wallet)
    async def admin_sell_wallet_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        data = await state.get_data()
        key = str(data.get("sell_wallet_key") or "").strip().lower()
        if key not in SELL_WALLET_LABELS:
            await state.clear()
            await message.answer("Сессия обновления кошелька сброшена.")
            return
        value = (message.text or "").strip()
        if len(value) < 10 or len(value) > 256:
            await message.answer("Кошелек должен быть длиной от 10 до 256 символов.")
            return
        if not await ctx.settings.set_sell_wallet(key, value):
            await message.answer("Не удалось сохранить кошелек.")
            return
        await state.clear()
        await message.answer(f"Кошелек для {SELL_WALLET_LABELS[key]} обновлен.")
        await show_sell_wallets_panel(message)

    @router.callback_query(F.data == "admin:req:toggle_mode")
    async def admin_req_toggle_mode(callback: CallbackQuery) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await ctx.settings.toggle_requisites_mode()
        await callback.answer(f"Режим переключен: {mode_label()}")
        await show_requisites_panel(msg)

    @router.callback_query(F.data == "admin:req:edit_bank")
    async def admin_req_edit_bank(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        if ctx.settings.requisites_mode == "split":
            await msg.answer(
                "Выберите метод оплаты для редактирования реквизитов:",
                reply_markup=kb_admin_split_methods_pick(
                    ctx.settings.payment_methods(),
                    methods_configured_map(),
                ),
            )
            return
        await state.set_state(AdminState.waiting_admin_bank_name)
        await state.update_data(bank_mode="single")
        await msg.answer(
            "Отправьте новое название банка.\n"
            f"Текущее значение: <b>{ctx.settings.requisites_bank}</b>"
        )

    @router.callback_query(F.data.startswith("admin:req:edit_method:"))
    async def admin_req_edit_method(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        raw_index = (callback.data or "").split(":")[-1]
        if not raw_index.isdigit():
            await callback.answer("Некорректный индекс", show_alert=True)
            return
        index = int(raw_index)
        methods = ctx.settings.payment_methods()
        if index < 0 or index >= len(methods):
            await callback.answer("Способ оплаты не найден", show_alert=True)
            return
        method = methods[index]
        bank, value = ctx.settings.method_requisites(method)
        await callback.answer()
        await state.set_state(AdminState.waiting_admin_bank_name)
        await state.update_data(bank_mode="split_method", split_method=method)
        await msg.answer(
            f"Метод: <b>{method}</b>\n"
            f"Текущий банк: <b>{bank}</b>\n"
            f"Текущие реквизиты: <code>{value}</code>\n\n"
            "Отправьте новый банк:"
        )

    @router.message(AdminState.waiting_admin_bank_name)
    async def admin_bank_name_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        bank = (message.text or "").strip()
        if len(bank) < 2 or len(bank) > 64:
            await message.answer("Название банка должно быть от 2 до 64 символов.")
            return
        data = await state.get_data()
        mode = data.get("bank_mode")
        if mode == "split_method":
            method = str(data.get("split_method") or "")
            _, old_value = ctx.settings.method_requisites(method)
            await ctx.settings.set_method_requisites(method, bank, old_value)
            await state.set_state(AdminState.waiting_admin_requisites_value)
            await state.update_data(req_mode="split_method", split_method=method)
            await message.answer("Теперь отправьте реквизиты для этого метода.")
            return
        await ctx.settings.set_requisites_bank(bank)
        await state.clear()
        await message.answer("Банк обновлен.")
        await show_requisites_panel(message)

    @router.callback_query(F.data == "admin:req:edit_value")
    async def admin_req_edit_value(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        if ctx.settings.requisites_mode == "split":
            await msg.answer(
                "Выберите метод оплаты для редактирования реквизитов:",
                reply_markup=kb_admin_split_methods_pick(
                    ctx.settings.payment_methods(),
                    methods_configured_map(),
                ),
            )
            return
        await state.set_state(AdminState.waiting_admin_requisites_value)
        await state.update_data(req_mode="single")
        await msg.answer(
            "Отправьте новые реквизиты.\n"
            f"Текущий банк: <b>{ctx.settings.requisites_bank}</b>\n"
            f"Текущее значение: <code>{ctx.settings.requisites_value}</code>"
        )

    @router.message(AdminState.waiting_admin_requisites_value)
    async def admin_req_value_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        value = (message.text or "").strip()
        if len(value) < 6 or len(value) > 128:
            await message.answer("Реквизиты должны быть длиной от 6 до 128 символов.")
            return
        data = await state.get_data()
        if data.get("req_mode") == "split_method":
            method = str(data.get("split_method") or "")
            bank, _ = ctx.settings.method_requisites(method)
            await ctx.settings.set_method_requisites(method, bank, value)
        else:
            await ctx.settings.set_requisites_value(value)
        await state.clear()
        await message.answer("Реквизиты обновлены.")
        await show_requisites_panel(message)

    @router.callback_query(F.data == "admin:req:add_method")
    async def admin_req_add_method(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        await callback.answer()
        await state.set_state(AdminState.waiting_admin_payment_method_add)
        await msg.answer("Введите название нового способа оплаты.")

    @router.message(AdminState.waiting_admin_payment_method_add)
    async def admin_req_add_method_input(message: Message, state: FSMContext) -> None:
        user_id = message_user_id(message)
        if user_id is None or not ctx.is_admin(user_id):
            await message.answer("Доступ запрещен.")
            return
        value = (message.text or "").strip()
        if not await ctx.settings.add_payment_method(value):
            await message.answer("Не удалось добавить способ оплаты (проверьте длину/дубликаты).")
            return
        await state.clear()
        await message.answer(f"Способ оплаты добавлен: <b>{value}</b>")
        await show_requisites_panel(message)

    @router.callback_query(F.data == "admin:req:delete_method_menu")
    async def admin_req_delete_method_menu(callback: CallbackQuery) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        methods = ctx.settings.payment_methods()
        if len(methods) <= 1:
            await callback.answer("Нельзя удалить единственный способ оплаты.", show_alert=True)
            return
        await callback.answer()
        await msg.answer(
            "Выберите способ оплаты для удаления:",
            reply_markup=kb_admin_delete_payment_method_with_status(
                methods,
                methods_configured_map(),
            ),
        )

    @router.callback_query(F.data.startswith("admin:req:del_method:"))
    async def admin_req_del_method(callback: CallbackQuery) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        raw_index = (callback.data or "").split(":")[-1]
        if not raw_index.isdigit():
            await callback.answer("Некорректный индекс", show_alert=True)
            return
        index = int(raw_index)
        methods = ctx.settings.payment_methods()
        if index < 0 or index >= len(methods):
            await callback.answer("Способ оплаты не найден", show_alert=True)
            return
        method_name = methods[index]
        if not await ctx.settings.delete_payment_method(index):
            await callback.answer("Нельзя удалить способ оплаты.", show_alert=True)
            return
        await callback.answer("Удалено")
        await msg.answer(f"Способ оплаты удален: <b>{method_name}</b>")
        await show_requisites_panel(msg)

    @router.callback_query(F.data == "admin:rates")
    async def admin_rates(callback: CallbackQuery) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        rates = await ctx.rates.get_rates(force=True)
        lines = []
        for symbol in COINS:
            val = rates.get(symbol.lower())
            if val:
                lines.append(f"{COINS[symbol]['symbol']}: {fmt_money(val)} RUB")
        
        await callback.answer("Курсы обновлены")
        await msg.answer("🔄 Курсы обновлены:\n" + "\n".join(lines))

    @router.callback_query(F.data.startswith("admin:order:confirm:"))
    async def admin_confirm_order(callback: CallbackQuery) -> None:
        user_id = callback_user_id(callback)
        msg = callback_message(callback)
        if user_id is None or msg is None or not ctx.is_admin(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        order_id = (callback.data or "").split(":")[-1]
        success, order = await ctx.orders.confirm_order(order_id, user_id)
        if not success or order is None:
            await callback.answer("Нельзя подтвердить эту заявку.", show_alert=True)
            return
        await callback.answer("Выдача подтверждена")

        username = safe_username(order["username"])
        text = (
            "✅ Выдача подтверждена\n\n"
            f"📦 ID заказа: {order['order_id']}\n"
            f"👤 ID: {order['user_id']}\n"
            f"📝 Username: {username}\n"
            "👛 Кошелек:\n"
            f"{order['wallet']}\n\n"
            f"💎 Крипта: {fmt_coin(order['coin_amount'])} {order['coin_symbol']}\n"
            f"💰 Сумма: {int(order['amount_rub'])} RUB\n"
            f"💳 Способ оплаты: {order['payment_method']}"
        )
        try:
            if msg.photo or msg.video or msg.document or msg.audio or msg.voice:
                await msg.edit_caption(caption=text)
            else:
                await msg.edit_text(text)
        except Exception:
            await msg.answer(text)

        try:
            bot = callback.bot
            if bot is None:
                return
            await bot.send_message(
                chat_id=order["user_id"],
                text=(
                    f"✅ Ваша заявка #{order['order_id']} подтверждена.\n"
                    "Средства отправлены. Спасибо за обмен."
                ),
            )
        except Exception:
            pass

    return router
