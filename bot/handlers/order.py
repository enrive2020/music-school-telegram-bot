"""Анкета записи на пробное занятие (FSM).

Поток шагов:
    [карточка] → Записаться → for_whom → name → phone → time → comment → confirm

Два способа показать очередной шаг:
    • переход по кнопке (callback) — РЕДАКТИРУЕМ то же сообщение (чисто, один экран);
    • переход после текстового ввода — шлём НОВОЕ сообщение, предварительно
      сняв кнопки со старого, чтобы их нельзя было нажать повторно.
Эти два механизма спрятаны в helpers _prompt_via_edit / _prompt_via_send.

В Фазе 3 подтверждение — заглушка: сохранение в БД будет в Фазе 5,
уведомление админам — в Фазе 7, валидация ввода — в Фазе 4.
"""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.catalog import Catalog
from bot.handlers.catalog import direction_text
from bot.keyboards.catalog import direction_details_keyboard
from bot.keyboards.order import (
    ORDER_BACK,
    ORDER_CANCEL,
    ORDER_CONFIRM,
    ORDER_SKIP,
    ForWhomCallback,
    StartOrderCallback,
    comment_keyboard,
    confirm_keyboard,
    for_whom_keyboard,
    text_step_keyboard,
)
from bot.states.order import OrderForm

router = Router(name="order")

# ── Тексты шагов ──────────────────────────────────────────────────
FOR_WHOM_TEXT = "Шаг 1 из 5. Для кого занятие?"
NAME_TEXT = "Шаг 2 из 5. Как вас зовут? Напишите имя."
PHONE_TEXT = "Шаг 3 из 5. Напишите номер телефона для связи."
TIME_TEXT = "Шаг 4 из 5. В какое время удобно? Например: «будни после 18:00»."
COMMENT_TEXT = "Шаг 5 из 5. Комментарий или пожелание? Или нажмите «Пропустить»."

# Человеческие подписи для сохранённых кодов «для кого».
FOR_WHOM_LABELS = {"child": "Ребёнок", "adult": "Взрослый"}


# ══════════════════════════════════════════════════════════════════
#  Helpers: как показать очередной шаг
# ══════════════════════════════════════════════════════════════════
async def _prompt_via_edit(message: Message, state: FSMContext, text: str, keyboard) -> None:
    """Переход по кнопке: редактируем текущее сообщение-приглашение."""
    await message.edit_text(text, reply_markup=keyboard)
    # Запоминаем id активного приглашения — понадобится, чтобы позже
    # снять с него кнопки при текстовом переходе.
    await state.update_data(prompt_id=message.message_id)


async def _prompt_via_send(message: Message, state: FSMContext, text: str, keyboard) -> None:
    """Переход после текстового ввода: шлём новое приглашение,
    предварительно сняв кнопки со старого."""
    data = await state.get_data()
    old_id = data.get("prompt_id")
    if old_id:
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id, message_id=old_id, reply_markup=None
            )
        except TelegramBadRequest:
            # Сообщение слишком старое или уже без кнопок — не критично.
            pass
    sent = await message.answer(text, reply_markup=keyboard)
    await state.update_data(prompt_id=sent.message_id)


def _require_message(callback: CallbackQuery) -> Message | None:
    """Достаёт сообщение из callback, если оно ещё доступно для правки."""
    return callback.message if isinstance(callback.message, Message) else None


def _confirm_text(data: dict, catalog: Catalog) -> str:
    """Собирает итоговую сводку заявки для экрана подтверждения."""
    direction = catalog.get_direction(data["direction_id"])
    direction_label = direction.button_label if direction else data["direction_id"]
    comment = data.get("comment") or "—"
    return (
        "<b>Проверьте заявку</b>\n\n"
        f"Направление: {direction_label}\n"
        f"Для кого: {FOR_WHOM_LABELS.get(data['for_whom'], data['for_whom'])}\n"
        f"Имя: {data['name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Удобное время: {data['time']}\n"
        f"Комментарий: {comment}\n\n"
        "Всё верно?"
    )


def _clean_text(message: Message) -> str | None:
    """Базовая защита текстового ввода (полная валидация — Фаза 4).

    Отсекаем пустые сообщения, не-текст (стикеры, фото) и команды,
    чтобы не сохранить «/help» как имя клиента. None → просим повторить.
    """
    if not message.text:
        return None
    text = message.text.strip()
    if not text or text.startswith("/"):
        return None
    return text


# ══════════════════════════════════════════════════════════════════
#  Вход в анкету и переходы вперёд
# ══════════════════════════════════════════════════════════════════
@router.callback_query(StartOrderCallback.filter())
async def start_order(
    callback: CallbackQuery,
    callback_data: StartOrderCallback,
    state: FSMContext,
) -> None:
    """Клик «Записаться» на карточке — начинаем анкету."""
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return

    # Сбрасываем возможную прошлую незаконченную анкету и кладём в
    # хранилище FSM выбранное направление.
    await state.clear()
    await state.update_data(direction_id=callback_data.direction_id)
    await state.set_state(OrderForm.for_whom)
    await _prompt_via_edit(message, state, FOR_WHOM_TEXT, for_whom_keyboard())
    await callback.answer()


@router.callback_query(ForWhomCallback.filter(), StateFilter(OrderForm.for_whom))
async def choose_for_whom(
    callback: CallbackQuery,
    callback_data: ForWhomCallback,
    state: FSMContext,
) -> None:
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return

    await state.update_data(for_whom=callback_data.value)
    await state.set_state(OrderForm.name)
    await _prompt_via_edit(message, state, NAME_TEXT, text_step_keyboard())
    await callback.answer()


@router.message(OrderForm.name)
async def enter_name(message: Message, state: FSMContext) -> None:
    name = _clean_text(message)
    if name is None:
        await message.answer("Напишите, пожалуйста, имя текстом.")
        return
    await state.update_data(name=name)
    await state.set_state(OrderForm.phone)
    await _prompt_via_send(message, state, PHONE_TEXT, text_step_keyboard())


@router.message(OrderForm.phone)
async def enter_phone(message: Message, state: FSMContext) -> None:
    phone = _clean_text(message)
    if phone is None:
        await message.answer("Напишите, пожалуйста, номер телефона текстом.")
        return
    await state.update_data(phone=phone)
    await state.set_state(OrderForm.time)
    await _prompt_via_send(message, state, TIME_TEXT, text_step_keyboard())


@router.message(OrderForm.time)
async def enter_time(message: Message, state: FSMContext) -> None:
    time = _clean_text(message)
    if time is None:
        await message.answer("Напишите, пожалуйста, удобное время текстом.")
        return
    await state.update_data(time=time)
    await state.set_state(OrderForm.comment)
    await _prompt_via_send(message, state, COMMENT_TEXT, comment_keyboard())


@router.message(OrderForm.comment)
async def enter_comment(message: Message, state: FSMContext, catalog: Catalog) -> None:
    comment = _clean_text(message)
    if comment is None:
        await message.answer("Напишите комментарий текстом или нажмите «Пропустить».")
        return
    await state.update_data(comment=comment)
    await _go_to_confirm_via_send(message, state, catalog)


@router.callback_query(F.data == ORDER_SKIP, StateFilter(OrderForm.comment))
async def skip_comment(callback: CallbackQuery, state: FSMContext, catalog: Catalog) -> None:
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return
    await state.update_data(comment="")
    await state.set_state(OrderForm.confirm)
    data = await state.get_data()
    await _prompt_via_edit(message, state, _confirm_text(data, catalog), confirm_keyboard())
    await callback.answer()


async def _go_to_confirm_via_send(message: Message, state: FSMContext, catalog: Catalog) -> None:
    await state.set_state(OrderForm.confirm)
    data = await state.get_data()
    await _prompt_via_send(message, state, _confirm_text(data, catalog), confirm_keyboard())


# ══════════════════════════════════════════════════════════════════
#  Навигация: назад / отмена / подтверждение
# ══════════════════════════════════════════════════════════════════
@router.callback_query(F.data == ORDER_BACK, StateFilter(OrderForm))
async def go_back(callback: CallbackQuery, state: FSMContext, catalog: Catalog) -> None:
    """Один шаг назад. Целевой шаг зависит от ТЕКУЩЕГО состояния,
    поэтому кнопка «Назад» всегда одна и та же, а логика — здесь."""
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return

    current = await state.get_state()

    if current == OrderForm.for_whom.state:
        # Назад с первого шага = выход из анкеты обратно на карточку направления.
        data = await state.get_data()
        direction_id = data["direction_id"]
        await state.clear()
        text = direction_text(catalog, direction_id)
        if text is not None:
            await message.edit_text(
                text, reply_markup=direction_details_keyboard(direction_id)
            )
    elif current == OrderForm.name.state:
        await state.set_state(OrderForm.for_whom)
        await _prompt_via_edit(message, state, FOR_WHOM_TEXT, for_whom_keyboard())
    elif current == OrderForm.phone.state:
        await state.set_state(OrderForm.name)
        await _prompt_via_edit(message, state, NAME_TEXT, text_step_keyboard())
    elif current == OrderForm.time.state:
        await state.set_state(OrderForm.phone)
        await _prompt_via_edit(message, state, PHONE_TEXT, text_step_keyboard())
    elif current == OrderForm.comment.state:
        await state.set_state(OrderForm.time)
        await _prompt_via_edit(message, state, TIME_TEXT, text_step_keyboard())
    elif current == OrderForm.confirm.state:
        await state.set_state(OrderForm.comment)
        await _prompt_via_edit(message, state, COMMENT_TEXT, comment_keyboard())

    await callback.answer()


@router.callback_query(F.data == ORDER_CANCEL, StateFilter(OrderForm))
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    message = _require_message(callback)
    await state.clear()
    if message is not None:
        await message.edit_text(
            "Заявка отменена. Чтобы записаться заново — отправьте /start."
        )
    await callback.answer()


@router.message(Command("cancel"), StateFilter(OrderForm))
async def cancel_order_command(message: Message, state: FSMContext) -> None:
    """Текстовый аналог кнопки «Отмена» — на случай, если клиент
    печатает /cancel вместо нажатия кнопки."""
    await state.clear()
    await message.answer("Заявка отменена. Чтобы записаться заново — отправьте /start.")


@router.callback_query(F.data == ORDER_CONFIRM, StateFilter(OrderForm.confirm))
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    message = _require_message(callback)
    # Фаза 3 — заглушка. В Фазе 5 здесь появится сохранение в SQLite,
    # в Фазе 7 — уведомление администраторам.
    await state.clear()
    if message is not None:
        await message.edit_text(
            "✅ Заявка оформлена!\n\n"
            "Спасибо, мы свяжемся с вами.\n"
            "<i>(Сохранение и уведомление админам добавим на следующих этапах.)</i>"
        )
    await callback.answer("Готово!")


# ══════════════════════════════════════════════════════════════════
#  Устаревшие кнопки: клиент нажал кнопку из старого сообщения,
#  когда анкета уже завершена или не в том состоянии. Не молчим.
#  Регистрируется ПОСЛЕ рабочих обработчиков — поэтому ловит только
#  то, что не подошло по состоянию выше.
# ══════════════════════════════════════════════════════════════════
@router.callback_query(F.data.in_({ORDER_BACK, ORDER_CANCEL, ORDER_CONFIRM, ORDER_SKIP}))
async def stale_nav(callback: CallbackQuery) -> None:
    await callback.answer(
        "Эта форма уже завершена. Отправьте /start, чтобы начать заново.",
        show_alert=True,
    )


@router.callback_query(ForWhomCallback.filter())
async def stale_for_whom(callback: CallbackQuery) -> None:
    await callback.answer(
        "Эта форма уже завершена. Отправьте /start, чтобы начать заново.",
        show_alert=True,
    )
