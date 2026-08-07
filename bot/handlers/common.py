"""Помощники, нужные нескольким группам обработчиков."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, ReplyKeyboardRemove


async def drop_reply_keyboard(message: Message) -> None:
    """Убирает reply-клавиатуру снизу экрана.

    Ограничение Telegram: клавиатуру нельзя убрать «просто так» —
    только вместе с каким-нибудь сообщением. Поэтому отправляем
    служебное сообщение и сразу удаляем его.
    """
    try:
        tmp = await message.answer("⌛", reply_markup=ReplyKeyboardRemove())
        await tmp.delete()
    except TelegramBadRequest:
        # Не смогли — не страшно: клавиатура просто останется видимой.
        pass
