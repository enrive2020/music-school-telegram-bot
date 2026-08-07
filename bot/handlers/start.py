"""Команда /start — точка входа клиента: приветствие + меню направлений."""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.catalog import Catalog
from bot.handlers.catalog import menu_text
from bot.keyboards.catalog import directions_keyboard

router = Router(name="start")


# Параметр catalog появляется «из ниоткуда»? Нет: в main.py мы передали
# каталог в Dispatcher(catalog=...), и aiogram подставляет его в любой
# хендлер, который объявил параметр с таким именем. Это dependency
# injection: хендлер не знает, ОТКУДА берётся каталог, — и потому его
# легко тестировать, подсунув каталог из трёх строчек.
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, catalog: Catalog) -> None:
    # /start в любой момент = «начать сначала»: сбрасываем незаконченную
    # анкету, чтобы клиент не остался запертым в состоянии ввода.
    await state.clear()

    user_name = message.from_user.first_name if message.from_user else "друг"

    await message.answer(
        f"Привет, {user_name}! 👋\n"
        "Здесь можно записаться на пробное занятие.\n\n" + menu_text(catalog),
        reply_markup=directions_keyboard(catalog),
    )
