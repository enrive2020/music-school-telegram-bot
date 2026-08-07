"""Анкета записи на пробное занятие (FSM).

Поток шагов:
    [карточка] → Записаться → for_whom → name → phone → time → comment → confirm

Два способа показать очередной шаг:
    • переход по кнопке (callback) — РЕДАКТИРУЕМ то же сообщение (чисто, один экран);
    • переход после текстового ввода — шлём НОВОЕ сообщение, предварительно
      сняв кнопки со старого, чтобы их нельзя было нажать повторно.
Эти два механизма спрятаны в helpers _prompt_via_edit / _prompt_via_send.

Шаг «телефон» — особый: там reply-клавиатура с кнопкой запроса контакта,
поэтому на него всегда заходим НОВЫМ сообщением, а при уходе клавиатуру
убираем (см. _enter_phone_step и drop_reply_keyboard).

В Фазе 4 подтверждение — по-прежнему заглушка: сохранение в БД
будет в Фазе 5, уведомление админам — в Фазе 7.
"""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.catalog import Catalog
from bot.handlers.catalog import direction_text
from bot.handlers.common import drop_reply_keyboard
from bot.keyboards.catalog import direction_details_keyboard
from bot.keyboards.order import (
    NAV_BACK_BTN,
    NAV_CANCEL_BTN,
    ORDER_BACK,
    ORDER_CANCEL,
    ORDER_CONFIRM,
    ORDER_SKIP,
    ForWhomCallback,
    StartOrderCallback,
    comment_keyboard,
    confirm_keyboard,
    for_whom_keyboard,
    phone_keyboard,
    text_step_keyboard,
)
from bot.states.order import OrderForm
from bot.validators import (
    ValidationError,
    format_phone_display,
    validate_comment,
    validate_name,
    validate_phone,
    validate_time,
)

router = Router(name="order")

# ── Тексты шагов ──────────────────────────────────────────────────
FOR_WHOM_TEXT = "Шаг 1 из 5. Для кого занятие?"
NAME_TEXT = "Шаг 2 из 5. Как вас зовут? Напишите имя."
PHONE_TEXT = (
    "Шаг 3 из 5. Оставьте номер телефона.\n\n"
    "Нажмите «📱 Отправить мой номер» — или напишите его вручную."
)
TIME_TEXT = "Шаг 4 из 5. В какое время удобно? Например: «будни после 18:00»."
COMMENT_TEXT = "Шаг 5 из 5. Комментарий или пожелание? Или нажмите «Пропустить»."

CANCEL_TEXT = "Заявка отменена. Чтобы записаться заново — отправьте /start."
STALE_TEXT = "Эта форма уже завершена. Отправьте /start, чтобы начать заново."

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


async def _clear_old_prompt(message: Message, state: FSMContext) -> None:
    """Снимает кнопки с предыдущего приглашения, чтобы их нельзя
    было нажать повторно после того, как шаг уже пройден."""
    data = await state.get_data()
    old_id = data.get("prompt_id")
    if not old_id:
        return
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id, message_id=old_id, reply_markup=None
        )
    except TelegramBadRequest:
        # Сообщение слишком старое, уже без кнопок или это было
        # сообщение с reply-клавиатурой — не критично.
        pass


async def _prompt_via_send(message: Message, state: FSMContext, text: str, keyboard) -> None:
    """Переход после текстового ввода: шлём новое приглашение,
    предварительно сняв кнопки со старого."""
    await _clear_old_prompt(message, state)
    sent = await message.answer(text, reply_markup=keyboard)
    await state.update_data(prompt_id=sent.message_id)


async def _enter_phone_step(message: Message, state: FSMContext) -> None:
    """Заходит на шаг телефона.

    Всегда НОВЫМ сообщением: reply-клавиатуру невозможно навесить
    через edit_text — только при отправке.
    """
    await _clear_old_prompt(message, state)
    await state.set_state(OrderForm.phone)
    sent = await message.answer(PHONE_TEXT, reply_markup=phone_keyboard())
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
        # Храним E.164, а показываем в читаемом виде.
        f"Телефон: {format_phone_display(data['phone'])}\n"
        f"Удобное время: {data['time']}\n"
        f"Комментарий: {comment}\n\n"
        "Всё верно?"
    )


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
    try:
        # message.text is None для стикеров, фото, голосовых —
        # валидатор получит пустую строку и вернёт понятную ошибку.
        name = validate_name(message.text or "")
    except ValidationError as e:
        # Остаёмся в том же состоянии: клиент просто пробует ещё раз.
        await message.answer(str(e))
        return

    await state.update_data(name=name)
    await _enter_phone_step(message, state)


# ── Шаг телефона: контакт, кнопки навигации и ручной ввод ─────────
# Порядок регистрации важен: сначала специфичные фильтры, в самом
# конце — общий обработчик текста.


@router.message(OrderForm.phone, F.contact)
async def enter_phone_contact(
    message: Message, state: FSMContext, catalog: Catalog
) -> None:
    """Клиент нажал «Отправить мой номер» — Telegram прислал контакт."""
    raw = message.contact.phone_number if message.contact else ""
    try:
        phone = validate_phone(raw, catalog.school.phone_region)
    except ValidationError as e:
        # Крайне маловероятно (номер приходит от Telegram), но
        # доверять внешним данным вслепую нельзя.
        await message.answer(str(e))
        return

    await state.update_data(phone=phone)
    await drop_reply_keyboard(message)
    await state.set_state(OrderForm.time)
    await _prompt_via_send(message, state, TIME_TEXT, text_step_keyboard())


@router.message(OrderForm.phone, F.text == NAV_BACK_BTN)
async def phone_back(message: Message, state: FSMContext) -> None:
    """«Назад» с шага телефона — reply-кнопка приходит как текст."""
    await _clear_old_prompt(message, state)
    await drop_reply_keyboard(message)
    await state.set_state(OrderForm.name)
    sent = await message.answer(NAME_TEXT, reply_markup=text_step_keyboard())
    await state.update_data(prompt_id=sent.message_id)


@router.message(OrderForm.phone, F.text == NAV_CANCEL_BTN)
async def phone_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())


@router.message(OrderForm.phone)
async def enter_phone_text(
    message: Message, state: FSMContext, catalog: Catalog
) -> None:
    """Номер, введённый вручную."""
    try:
        phone = validate_phone(message.text or "", catalog.school.phone_region)
    except ValidationError as e:
        await message.answer(str(e))
        return

    await state.update_data(phone=phone)
    await drop_reply_keyboard(message)
    await state.set_state(OrderForm.time)
    await _prompt_via_send(message, state, TIME_TEXT, text_step_keyboard())


@router.message(OrderForm.time)
async def enter_time(message: Message, state: FSMContext) -> None:
    try:
        time = validate_time(message.text or "")
    except ValidationError as e:
        await message.answer(str(e))
        return

    await state.update_data(time=time)
    await state.set_state(OrderForm.comment)
    await _prompt_via_send(message, state, COMMENT_TEXT, comment_keyboard())


@router.message(OrderForm.comment)
async def enter_comment(message: Message, state: FSMContext, catalog: Catalog) -> None:
    try:
        comment = validate_comment(message.text or "")
    except ValidationError as e:
        await message.answer(str(e))
        return

    if not comment:
        # Пустое сообщение или стикер на необязательном шаге —
        # подсказываем, что есть кнопка «Пропустить».
        await message.answer("Напишите комментарий текстом или нажмите «Пропустить».")
        return

    await state.update_data(comment=comment)
    await state.set_state(OrderForm.confirm)
    data = await state.get_data()
    await _prompt_via_send(message, state, _confirm_text(data, catalog), confirm_keyboard())


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
        # Штатно «Назад» на этом шаге — reply-кнопка (см. phone_back).
        # Ветка нужна на случай нажатия inline-кнопки из старого сообщения.
        await drop_reply_keyboard(message)
        await state.set_state(OrderForm.name)
        await _prompt_via_edit(message, state, NAME_TEXT, text_step_keyboard())
    elif current == OrderForm.time.state:
        # Возврат на шаг телефона: нужна reply-клавиатура, а её
        # нельзя навесить через edit — уходим новым сообщением.
        await _enter_phone_step(message, state)
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
        await message.edit_text(CANCEL_TEXT)
    await callback.answer()


@router.message(Command("cancel"), StateFilter(OrderForm))
async def cancel_order_command(message: Message, state: FSMContext) -> None:
    """Текстовый аналог кнопки «Отмена» — на случай, если клиент
    печатает /cancel вместо нажатия кнопки."""
    await state.clear()
    # ReplyKeyboardRemove на случай, если отмена пришла с шага телефона.
    await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())


@router.callback_query(F.data == ORDER_CONFIRM, StateFilter(OrderForm.confirm))
async def confirm_order(callback: CallbackQuery, state: FSMContext) -> None:
    message = _require_message(callback)
    # Фаза 4 — по-прежнему заглушка. В Фазе 5 здесь появится сохранение
    # в SQLite, в Фазе 7 — уведомление администраторам.
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
    await callback.answer(STALE_TEXT, show_alert=True)


@router.callback_query(ForWhomCallback.filter())
async def stale_for_whom(callback: CallbackQuery) -> None:
    await callback.answer(STALE_TEXT, show_alert=True)
