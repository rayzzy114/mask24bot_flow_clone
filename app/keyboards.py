from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .constants import LINK_LABELS, SELL_WALLET_LABELS


def kb_admin_order_confirm(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить выдачу",
                    callback_data=f"admin:order:confirm:{order_id}",
                )
            ]
        ]
    )


def kb_admin_panel(commission_percent: float) -> InlineKeyboardMarkup:
    link_keys = list(LINK_LABELS.keys())
    link_rows = []
    for index in range(0, len(link_keys), 2):
        row_keys = link_keys[index : index + 2]
        link_rows.append(
            [
                InlineKeyboardButton(text=LINK_LABELS[key], callback_data=f"admin:set_link:{key}")
                for key in row_keys
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Комиссия: {commission_percent:.2f}%", callback_data="admin:set_commission")],
            [InlineKeyboardButton(text="💳 Управление реквизитами", callback_data="admin:requisites")],
            [InlineKeyboardButton(text="⚙️ ENV KEY=VALUE", callback_data="admin:set_env")],
            *link_rows,
            [InlineKeyboardButton(text="🪙 Кошельки продажи", callback_data="admin:sell_wallets")],
        ]
    )


def kb_admin_requisites(commission_percent: float, mode_label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🔃 Режим: {mode_label}", callback_data="admin:req:toggle_mode")],
            [
                InlineKeyboardButton(text="🏦 Банк", callback_data="admin:req:edit_bank"),
                InlineKeyboardButton(text="✏️ Реквизиты", callback_data="admin:req:edit_value"),
            ],
            [
                InlineKeyboardButton(
                    text=f"💵 Комиссия: {commission_percent:.2f}%",
                    callback_data="admin:req:commission",
                )
            ],
            [
                InlineKeyboardButton(text="➕ Способ оплаты", callback_data="admin:req:add_method"),
                InlineKeyboardButton(text="➖ Удалить способ", callback_data="admin:req:delete_method_menu"),
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="admin:req:back")],
        ]
    )


def kb_admin_delete_payment_method(methods: list[str]) -> InlineKeyboardMarkup:
    return kb_admin_delete_payment_method_with_status(methods, None)


def kb_admin_delete_payment_method_with_status(
    methods: list[str],
    configured: dict[str, bool] | None,
) -> InlineKeyboardMarkup:
    def status_icon(method: str) -> str:
        if configured is None:
            return "✅"
        return "✅" if configured.get(method, False) else "❌"

    rows = [
        [
            InlineKeyboardButton(
                text=f"{status_icon(title)} {title}",
                callback_data=f"admin:req:del_method:{index}",
            )
        ]
        for index, title in enumerate(methods)
    ]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="admin:requisites")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_split_methods_pick(
    methods: list[str],
    configured: dict[str, bool] | None = None,
) -> InlineKeyboardMarkup:
    def status_icon(method: str) -> str:
        if configured is None:
            return "✅"
        return "✅" if configured.get(method, False) else "❌"

    rows = [
        [
            InlineKeyboardButton(
                text=f"{status_icon(title)} {title}",
                callback_data=f"admin:req:edit_method:{index}",
            )
        ]
        for index, title in enumerate(methods)
    ]
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="admin:requisites")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def links_help_lines(links: dict[str, str]) -> str:
    lines = []
    for key, label in LINK_LABELS.items():
        lines.append(f"{label}: {links.get(key, '-')}")
    return "\n".join(lines)


def kb_admin_sell_wallets(wallets: dict[str, str]) -> InlineKeyboardMarkup:
    rows = []
    for key, label in SELL_WALLET_LABELS.items():
        value = wallets.get(key, "").strip()
        status = "✅" if value else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {label}",
                    callback_data=f"admin:sell_wallet:set:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="admin:sell_wallets:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
