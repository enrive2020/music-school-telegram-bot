"""Клавиатуры каталога: меню направлений и кнопка «назад».

Инлайн-кнопки — это кнопки ПОД сообщением. При нажатии Telegram
присылает боту callback_query с зашитой в кнопку строкой callback_data
(до 64 байт). Сообщений от имени пользователя при этом не появляется.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.catalog import Catalog
from bot.keyboards.order import StartOrderCallback


class DirectionCallback(CallbackData, prefix="dir"):
    """Фабрика callback_data для кнопок направлений.

    Превращает DirectionCallback(direction_id="guitar") в строку
    "dir:guitar" и обратно — с проверкой типов. Руками клеить
    и парсить такие строки — классический источник багов.
    """

    direction_id: str


# Кнопка «назад» одна и без параметров — фабрика не нужна,
# достаточно строковой константы.
BACK_TO_MENU = "back_to_menu"


def directions_keyboard(catalog: Catalog) -> InlineKeyboardMarkup:
    """Меню направлений, собранное из конфига."""
    builder = InlineKeyboardBuilder()
    for d in catalog.directions:
        builder.button(
            text=d.button_label,
            callback_data=DirectionCallback(direction_id=d.id),
        )
    # По 2 кнопки в ряд: на телефоне длинные названия не обрезаются,
    # а список не растягивается в простыню.
    builder.adjust(2)
    return builder.as_markup()


def direction_details_keyboard(direction_id: str) -> InlineKeyboardMarkup:
    """Клавиатура карточки направления: «Записаться» (вход в анкету) + «назад».

    direction_id зашивается в кнопку «Записаться», чтобы анкета сразу
    знала выбранное направление.
    """
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✍️ Записаться",
        callback_data=StartOrderCallback(direction_id=direction_id),
    )
    builder.button(text="← Назад к направлениям", callback_data=BACK_TO_MENU)
    builder.adjust(1)  # каждая кнопка на своей строке
    return builder.as_markup()
