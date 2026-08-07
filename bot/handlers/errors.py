"""Глобальный перехватчик ошибок.

Без него необработанное исключение в любом хендлере означает:
у клиента навсегда крутятся часики на кнопке, он не понимает,
что произошло, и уходит. В логе при этом трейсбек есть, но узнаём
мы о проблеме только когда кто-то пожалуется.

Задача обработчика — превратить любой сбой в понятное сообщение
клиенту и подробную запись в логе.
"""

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

router = Router(name="errors")
logger = logging.getLogger(__name__)

USER_MESSAGE = (
    "😔 Что-то пошло не так. Мы уже знаем о проблеме.\n"
    "Попробуйте ещё раз или отправьте /start, чтобы начать заново."
)


@router.errors()
async def handle_any_error(event: ErrorEvent) -> bool:
    """Ловит исключения из всех хендлеров.

    Возвращаем True — «ошибка обработана», чтобы aiogram не поднимал
    её выше и не останавливал polling. Бот должен пережить сбой
    в одном диалоге, не роняя все остальные.
    """
    # exc_info=True добавит полный трейсбек — по нему потом чинить.
    logger.exception(
        "Необработанная ошибка при обработке апдейта %s",
        event.update.event_type,
        exc_info=event.exception,
    )

    # Пытаемся ответить пользователю — но осторожно: сообщения может
    # не быть (например, ошибка пришла из callback), а отправка сама
    # может упасть. Вложенный сбой не должен уронить обработчик ошибок.
    try:
        if event.update.message is not None:
            await event.update.message.answer(USER_MESSAGE)
        elif event.update.callback_query is not None:
            # show_alert — всплывающее окно; заодно снимает «часики».
            await event.update.callback_query.answer(
                "Что-то пошло не так. Отправьте /start.", show_alert=True
            )
    except TelegramAPIError:
        logger.error("Не удалось сообщить пользователю об ошибке")

    return True
