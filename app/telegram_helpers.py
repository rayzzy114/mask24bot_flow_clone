from aiogram.types import CallbackQuery, Message


def callback_message(callback: CallbackQuery) -> Message | None:
    if isinstance(callback.message, Message):
        return callback.message
    return None


def callback_user_id(callback: CallbackQuery) -> int | None:
    if callback.from_user is None:
        return None
    return callback.from_user.id


def message_user_id(message: Message) -> int | None:
    if message.from_user is None:
        return None
    return message.from_user.id
