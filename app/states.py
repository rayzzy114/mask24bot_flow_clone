from aiogram.fsm.state import State, StatesGroup


class UserState(StatesGroup):
    waiting_buy_amount = State()
    waiting_buy_wallet = State()
    waiting_buy_receipt = State()
    waiting_calc_amount = State()
    waiting_promo = State()
    waiting_wallet_deposit_coin = State()
    waiting_address_coin = State()
    waiting_address_value = State()
    waiting_address_name = State()


class AdminState(StatesGroup):
    waiting_admin_commission = State()
    waiting_admin_env = State()
    waiting_admin_link = State()
    waiting_admin_sell_wallet = State()
    waiting_admin_requisites_value = State()
    waiting_admin_bank_name = State()
    waiting_admin_payment_method_add = State()
