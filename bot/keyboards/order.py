"""Клавиатуры анкеты записи: старт, выбор «для кого», навигация, подтверждение.

Навигационные кнопки (Назад/Отмена) одинаковы почти на всех шагах,
поэтому вынесены в helper _nav_row — чтобы не дублировать их в каждой
клавиатуре и менять оформление в одном месте.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

# ── callback_data без параметров: простые строковые константы ──────
ORDER_BACK = "order_back"        # шаг назад
ORDER_CANCEL = "order_cancel"    # отменить всю заявку
ORDER_CONFIRM = "order_confirm"  # подтвердить заявку
ORDER_SKIP = "order_skip"        # пропустить необязательный шаг (комментарий)


# ── callback_data с параметром: фабрики (см. пояснение в Фазе 2) ───
class StartOrderCallback(CallbackData, prefix="start_order"):
    """Кнопка «Записаться» на карточке направления. Несёт id направления,
    чтобы анкета знала, на что именно записывается клиент."""

    direction_id: str


class ForWhomCallback(CallbackData, prefix="whom"):
    """Выбор «для кого»: value = 'child' или 'adult'."""

    value: str


def _nav_row(builder: InlineKeyboardBuilder, *, with_back: bool = True) -> None:
    """Добавляет ряд навигации в конец клавиатуры."""
    buttons = []
    if with_back:
        buttons.append(("← Назад", ORDER_BACK))
    buttons.append(("✖️ Отмена", ORDER_CANCEL))
    for text, data in buttons:
        builder.button(text=text, callback_data=data)


def for_whom_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Ребёнок", callback_data=ForWhomCallback(value="child"))
    builder.button(text="Взрослый", callback_data=ForWhomCallback(value="adult"))
    _nav_row(builder)
    # Первый ряд — 2 кнопки выбора, второй — навигация.
    builder.adjust(2, 2)
    return builder.as_markup()


def text_step_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура шагов с текстовым вводом (имя, время)."""
    builder = InlineKeyboardBuilder()
    _nav_row(builder)
    builder.adjust(2)
    return builder.as_markup()


def comment_keyboard() -> InlineKeyboardMarkup:
    """Комментарий необязателен — добавляем «Пропустить»."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить →", callback_data=ORDER_SKIP)
    _nav_row(builder)
    builder.adjust(1, 2)
    return builder.as_markup()


def confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=ORDER_CONFIRM)
    _nav_row(builder)
    builder.adjust(1, 2)
    return builder.as_markup()


# ══════════════════════════════════════════════════════════════════
#  Reply-клавиатура шага «телефон»
#
#  Reply-кнопки заменяют обычную клавиатуру внизу экрана. Нужны здесь
#  по одной причине: кнопка запроса контакта (request_contact)
#  существует ТОЛЬКО в reply-клавиатуре — inline так не умеет.
#
#  У сообщения может быть либо inline-, либо reply-клавиатура, но не
#  обе сразу. Поэтому на этом шаге вся навигация тоже переезжает вниз,
#  а нажатия приходят обычными текстовыми сообщениями — их мы ловим
#  по точному совпадению текста.
# ══════════════════════════════════════════════════════════════════
PHONE_SHARE_BTN = "📱 Отправить мой номер"
NAV_BACK_BTN = "← Назад"
NAV_CANCEL_BTN = "✖️ Отмена"


def phone_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    # request_contact=True: по нажатию Telegram спросит у клиента
    # разрешение и пришлёт номер сам — без шанса на опечатку.
    builder.button(text=PHONE_SHARE_BTN, request_contact=True)
    builder.button(text=NAV_BACK_BTN)
    builder.button(text=NAV_CANCEL_BTN)
    builder.adjust(1, 2)
    return builder.as_markup(
        # Кнопки по высоте содержимого, а не во весь экран.
        resize_keyboard=True,
        # Подсказка в поле ввода, если клиент печатает вручную.
        input_field_placeholder="+7 900 123-45-67",
    )
