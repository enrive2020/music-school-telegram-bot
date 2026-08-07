"""Просмотр каталога: нажатия на кнопки направлений и «назад»."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from bot.catalog import Catalog
from bot.keyboards.catalog import (
    BACK_TO_MENU,
    DirectionCallback,
    direction_details_keyboard,
    directions_keyboard,
)

router = Router(name="catalog")


def menu_text(catalog: Catalog) -> str:
    """Текст над меню направлений (используется и в /start, и в «назад»)."""
    return (
        f"<b>{catalog.school.name}</b>\n\n"
        "Выбери направление — покажу цену и расскажу подробнее:"
    )


def direction_text(catalog: Catalog, direction_id: str) -> str | None:
    """Карточка направления. None — если id в каталоге больше нет."""
    d = catalog.get_direction(direction_id)
    if d is None:
        return None
    return (
        f"{d.emoji} <b>{d.title}</b>\n\n"
        f"{d.description}\n\n"
        f"⏱ Занятие: {d.lesson_minutes} мин\n"
        f"💰 Цена: {d.price_per_lesson} {catalog.school.currency}/занятие"
    )


# DirectionCallback.filter() пропускает только колбэки вида "dir:...".
# aiogram сам распарсит строку обратно в объект и передаст его
# в параметр callback_data — заметь, уже типизированным.
@router.callback_query(DirectionCallback.filter())
async def show_direction(
    callback: CallbackQuery,
    callback_data: DirectionCallback,
    catalog: Catalog,
) -> None:
    text = direction_text(catalog, callback_data.direction_id)

    if text is None:
        # Клиент нажал кнопку из старого сообщения, а направление
        # из конфига уже удалили. Не молчим и не падаем — объясняем.
        # show_alert=True — всплывающее окно вместо тихой плашки.
        await callback.answer(
            "Этого направления больше нет. Отправь /start — покажу актуальные.",
            show_alert=True,
        )
        return

    # Редактируем СВОЁ сообщение с меню, а не шлём новое: диалог
    # не засоряется, у клиента всегда один «экран» каталога.
    # isinstance-проверка: у очень старых сообщений (48ч+) Telegram
    # не даёт редактировать текст, callback.message приходит «пустым».
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text, reply_markup=direction_details_keyboard()
        )

    # Обязательный «квиток» для Telegram: колбэк обработан.
    # Без него у клиента на кнопке до 30 секунд крутятся часики.
    await callback.answer()


@router.callback_query(F.data == BACK_TO_MENU)
async def back_to_menu(callback: CallbackQuery, catalog: Catalog) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            menu_text(catalog), reply_markup=directions_keyboard(catalog)
        )
    await callback.answer()
