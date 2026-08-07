"""Команда /start — точка входа клиента в диалог с ботом."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

# Router — «мини-диспетчер» этого файла. Каждый кусок диалога живёт
# в своём роутере, а main.py собирает их вместе. Так обработчики
# не превращаются в один файл на 500 строк.
router = Router(name="start")


# Декоратор = фильтр: «эта функция вызывается, когда пришло сообщение,
# и это сообщение — команда /start». Никаких if message.text == "/start".
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    # message.from_user может отсутствовать в служебных апдейтах,
    # поэтому аккуратно достаём имя с запасным вариантом.
    user_name = message.from_user.first_name if message.from_user else "друг"

    await message.answer(
        f"Привет, {user_name}! 👋\n\n"
        "Это бот музыкальной школы. Здесь можно записаться "
        "на пробное занятие: выбрать направление, оставить контакты "
        "и удобное время.\n\n"
        "Меню направлений появится в следующей фазе разработки 🙂"
    )
