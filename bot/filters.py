"""Фильтры доступа.

Административные команды показывают персональные данные клиентов:
имена, телефоны, время визита. Сработай такая команда у постороннего —
это утечка. Поэтому доступ проверяется фильтром, а не «если что,
никто не догадается».
"""

import logging

from aiogram.filters import BaseFilter
from aiogram.types import Message

logger = logging.getLogger(__name__)


class IsAdminChat(BaseFilter):
    """Пропускает только сообщения из чата администраторов.

    Фильтр получает admin_chat_id тем же механизмом внедрения
    зависимостей, что и обработчики: значение передано в Dispatcher.

    Если ADMIN_CHAT_ID не задан, доступа нет НИ У КОГО. Это осознанно:
    «не настроено» должно означать «закрыто», а не «открыто всем».
    """

    async def __call__(self, message: Message, admin_chat_id: int | None) -> bool:
        if admin_chat_id is None:
            return False

        allowed = message.chat.id == admin_chat_id
        if not allowed and message.from_user:
            # Логируем отказы: попытка достучаться до чужих данных —
            # то, о чём владелец должен иметь возможность узнать.
            logger.warning(
                "Отказано в доступе к админ-команде: chat=%s user=%s",
                message.chat.id,
                message.from_user.id,
            )
        return allowed
