"""Клавиатуры каталога: меню направлений и кнопка «назад».

Инлайн-кнопки — это кнопки ПОД сообщением. При нажатии Telegram
присылает боту callback_query с зашитой в кнопку строкой callback_data
(до 64 байт). Сообщений от имени пользователя при этом не появляется.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.catalog import Catalog


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


def direction_details_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура карточки направления: пока только «назад».

    В Фазе 3 сюда добавится кнопка «Записаться» — вход в анкету FSM.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад к направлениям", callback_data=BACK_TO_MENU)
    return builder.as_markup()
