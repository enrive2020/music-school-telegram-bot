"""Служебные команды.

/chatid — показывает ID текущего чата. Нужна, чтобы узнать значение
для ADMIN_CHAT_ID: у групп ID отрицательный и нигде в интерфейсе
Telegram не показывается. Команда остаётся в проекте намеренно —
будущий заказчик сможет настроить бота сам, без программиста.
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="admin")
logger = logging.getLogger(__name__)

# Понятные названия типов чата вместо технических.
CHAT_TYPES = {
    "private": "личный чат",
    "group": "группа",
    "supergroup": "супергруппа",
    "channel": "канал",
}


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    chat = message.chat
    chat_type = CHAT_TYPES.get(chat.type, chat.type)

    lines = [
        "🆔 <b>Данные чата</b>\n",
        f"ID чата: <code>{chat.id}</code>",
        f"Тип: {chat_type}",
    ]
    if chat.title:
        lines.append(f"Название: {chat.title}")
    if message.from_user:
        lines.append(f"\nВаш личный ID: <code>{message.from_user.id}</code>")

    lines.append(
        "\n<i>Скопируйте ID чата в переменную ADMIN_CHAT_ID "
        "файла .env и перезапустите бота.</i>"
    )

    await message.answer("\n".join(lines))
    logger.info("Запрошен ID чата: %s (%s)", chat.id, chat.type)
