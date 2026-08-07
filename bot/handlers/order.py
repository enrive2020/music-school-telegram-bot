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

import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.catalog import Catalog
from bot.formatters import format_day_full, format_slot, hm_to_time
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
    DayCallback,
    ForWhomCallback,
    SlotCallback,
    StartOrderCallback,
    comment_keyboard,
    confirm_keyboard,
    day_keyboard,
    for_whom_keyboard,
    phone_keyboard,
    text_step_keyboard,
    time_slots_keyboard,
)
from bot.services.notify import AdminNotifier
from bot.services.schedule import ScheduleService
from bot.states.order import OrderForm
from bot.storage.models import NewOrder
from bot.storage.orders import OrderRepository
from bot.validators import (
    ValidationError,
    format_phone_display,
    validate_comment,
    validate_name,
    validate_phone,
    validate_time,
)

logger = logging.getLogger(__name__)

router = Router(name="order")

# ── Тексты шагов ──────────────────────────────────────────────────
FOR_WHOM_TEXT = "Шаг 1 из 6. Для кого занятие?"
NAME_TEXT = "Шаг 2 из 6. Как вас зовут? Напишите имя."
PHONE_TEXT = (
    "Шаг 3 из 6. Оставьте номер телефона.\n\n"
    "Нажмите «📱 Отправить мой номер» — или напишите его вручную."
)
DAY_TEXT = "Шаг 4 из 6. Выберите удобный день:"
COMMENT_TEXT = "Шаг 6 из 6. Комментарий или пожелание? Или нажмите «Пропустить»."

# Запасной путь: свободных слотов нет во всём горизонте.
NO_SLOTS_TEXT = (
    "Шаг 4 из 6. Сейчас свободных мест в расписании нет.\n\n"
    "Напишите, когда вам удобно, — администратор подберёт время "
    "и перезвонит. Например: «будни после 18:00»."
)

SLOT_GONE_TEXT = "Это время уже занято или прошло. Выберите другой день."
NO_SLOTS_LEFT_TEXT = "Свободных слотов в этот день не осталось. Выберите другой."
PRESS_BUTTON_TEXT = "Пожалуйста, выберите вариант кнопкой выше."


def time_text(day: date) -> str:
    """Заголовок экрана времени — с датой, чтобы клиент не терялся."""
    return f"Шаг 5 из 6. Выберите время на {format_day_full(day)}:"

CANCEL_TEXT = "Заявка отменена. Чтобы записаться заново — отправьте /start."
STALE_TEXT = "Эта форма уже завершена. Отправьте /start, чтобы начать заново."

# Человеческие подписи для сохранённых кодов «для кого».
FOR_WHOM_LABELS = {"child": "Ребёнок", "adult": "Взрослый"}


# ══════════════════════════════════════════════════════════════════
#  Helpers: как показать очередной шаг
# ══════════════════════════════════════════════════════════════════
async def _prompt_via_edit(message: Message, state: FSMContext, text: str, keyboard) -> None:
    """Переход по кнопке: редактируем текущее сообщение-приглашение."""
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as e:
        # «message is not modified» — клиент нажал кнопку дважды подряд
        # или вернулся на экран, который уже показан. Содержимое совпало,
        # менять нечего. Это не сбой: экран и так верный.
        if "message is not modified" not in str(e):
            raise
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


async def _enter_day_step(
    message: Message,
    state: FSMContext,
    schedule: ScheduleService,
    *,
    via_edit: bool,
) -> None:
    """Показывает выбор дня.

    via_edit=False — приходим с шага телефона, где висит reply-клавиатура:
    её надо снять, а reply-клавиатуру нельзя убрать через edit_text,
    поэтому уходим новым сообщением.
    via_edit=True — приходим кнопкой «Назад» с шага времени: оба экрана
    инлайновые, чище отредактировать текущее сообщение.
    """
    days = schedule.available_days()

    if not days:
        # Свободных мест нет во всём горизонте (каникулы, конец
        # расписания). Не оставляем клиента без выхода — переключаем
        # на свободный ввод. Это ОТДЕЛЬНОЕ состояние: иначе обработчик
        # текста ответил бы «нажмите кнопку», которой нет.
        logger.warning("Нет доступных слотов в горизонте записи")
        await state.set_state(OrderForm.time_freeform)
        text, keyboard = NO_SLOTS_TEXT, text_step_keyboard()
    else:
        await state.set_state(OrderForm.day)
        text, keyboard = DAY_TEXT, day_keyboard(days)

    if via_edit:
        await _prompt_via_edit(message, state, text, keyboard)
    else:
        await _prompt_via_send(message, state, text, keyboard)


async def _show_slots_or_back(
    callback: CallbackQuery,
    message: Message,
    state: FSMContext,
    schedule: ScheduleService,
    day: date,
    *,
    alert: str = SLOT_GONE_TEXT,
) -> None:
    """Показывает слоты дня, а если их не осталось — возвращает к дням.

    Единый путь для трёх ситуаций: клиент выбрал день, вернулся «Назад»
    с комментария или нажал на протухший слот. Во всех случаях день мог
    опустеть, пока клиент думал, и оставлять экран без кнопок нельзя.

    ВАЖНО: «квиток» callback.answer() отправляет эта функция — и всегда
    ровно один раз. Иначе легко продублировать ответ на один колбэк
    (Telegram отвергает повторный) или забыть его вовсе.
    """
    slots = schedule.available_slots(day)

    if not slots:
        await callback.answer(alert, show_alert=True)
        await _enter_day_step(message, state, schedule, via_edit=True)
        return

    await state.update_data(day=day.isoformat())
    await state.set_state(OrderForm.time)
    await _prompt_via_edit(message, state, time_text(day), time_slots_keyboard(day, slots))
    await callback.answer()


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
#  /cancel — регистрируется ПЕРВЫМ среди обработчиков сообщений.
#
#  В aiogram побеждает первый подошедший обработчик. Если бы команда
#  стояла ниже, «/cancel» на шаге имени попал бы в enter_name и
#  клиент получил бы «имя может состоять только из букв» вместо
#  отмены — фильтр по состоянию шире, чем фильтр по команде.
# ══════════════════════════════════════════════════════════════════
@router.message(Command("cancel"), StateFilter(OrderForm))
async def cancel_order_command(message: Message, state: FSMContext) -> None:
    """Текстовый аналог кнопки «Отмена»."""
    await state.clear()
    # ReplyKeyboardRemove на случай, если отмена пришла с шага телефона.
    await message.answer(CANCEL_TEXT, reply_markup=ReplyKeyboardRemove())


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
    message: Message,
    state: FSMContext,
    catalog: Catalog,
    schedule: ScheduleService,
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
    await _enter_day_step(message, state, schedule, via_edit=False)


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
    message: Message,
    state: FSMContext,
    catalog: Catalog,
    schedule: ScheduleService,
) -> None:
    """Номер, введённый вручную."""
    try:
        phone = validate_phone(message.text or "", catalog.school.phone_region)
    except ValidationError as e:
        await message.answer(str(e))
        return

    await state.update_data(phone=phone)
    await drop_reply_keyboard(message)
    await _enter_day_step(message, state, schedule, via_edit=False)


# ── Выбор дня и времени ───────────────────────────────────────────
@router.callback_query(DayCallback.filter(), StateFilter(OrderForm.day))
async def choose_day(
    callback: CallbackQuery,
    callback_data: DayCallback,
    state: FSMContext,
    schedule: ScheduleService,
) -> None:
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return

    try:
        day = date.fromisoformat(callback_data.value)
    except ValueError:
        # Данные из кнопки — снаружи, доверять им нельзя.
        await callback.answer(SLOT_GONE_TEXT, show_alert=True)
        await _enter_day_step(message, state, schedule, via_edit=True)
        return

    # Отвечает на колбэк сам — снаружи answer() дублировать нельзя.
    await _show_slots_or_back(
        callback, message, state, schedule, day, alert=NO_SLOTS_LEFT_TEXT
    )


@router.callback_query(SlotCallback.filter(), StateFilter(OrderForm.time))
async def choose_slot(
    callback: CallbackQuery,
    callback_data: SlotCallback,
    state: FSMContext,
    schedule: ScheduleService,
) -> None:
    message = _require_message(callback)
    if message is None:
        await callback.answer("Сообщение устарело, отправьте /start.", show_alert=True)
        return

    slot = hm_to_time(callback_data.hm)
    try:
        day = date.fromisoformat(callback_data.day)
    except ValueError:
        day = None

    # Проверяем по актуальному расписанию, а не по тому, что пришло
    # из кнопки: сообщение могло пролежать час, и слот уже прошёл.
    if day is None or slot is None or not schedule.is_slot_available(day, slot):
        await callback.answer(SLOT_GONE_TEXT, show_alert=True)
        await _enter_day_step(message, state, schedule, via_edit=True)
        return

    # В data кладём и машинные значения (для перерисовки при «Назад»),
    # и готовую строку — её увидит клиент, владелец и таблица.
    await state.update_data(
        day=day.isoformat(),
        slot_hm=callback_data.hm,
        time=format_slot(day, slot),
    )
    await state.set_state(OrderForm.comment)
    await _prompt_via_edit(message, state, COMMENT_TEXT, comment_keyboard())
    await callback.answer()


@router.message(OrderForm.time_freeform)
async def enter_time_freeform(message: Message, state: FSMContext) -> None:
    """Запасной путь: слотов нет, клиент пишет время текстом."""
    try:
        preferred = validate_time(message.text or "")
    except ValidationError as e:
        await message.answer(str(e))
        return

    await state.update_data(time=preferred)
    await state.set_state(OrderForm.comment)
    await _prompt_via_send(message, state, COMMENT_TEXT, comment_keyboard())


# Текст вместо нажатия кнопки на шагах выбора. Регистрируется ПОСЛЕ
# callback-обработчиков и не затрагивает time_freeform — там текст ждут.
@router.message(StateFilter(OrderForm.day, OrderForm.time))
async def press_button_hint(message: Message) -> None:
    await message.answer(PRESS_BUTTON_TEXT)


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
async def go_back(
    callback: CallbackQuery,
    state: FSMContext,
    catalog: Catalog,
    schedule: ScheduleService,
) -> None:
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
    elif current in (OrderForm.day.state, OrderForm.time_freeform.state):
        # Возврат на шаг телефона: нужна reply-клавиатура, а её
        # нельзя навесить через edit — уходим новым сообщением.
        await _enter_phone_step(message, state)
    elif current == OrderForm.time.state:
        # Со слотов — обратно к списку дней. Оба экрана инлайновые.
        await _enter_day_step(message, state, schedule, via_edit=True)
    elif current == OrderForm.comment.state:
        # Возврат на выбор времени. Если день выбирался кнопками —
        # пересобираем сетку под него; слоты могли протухнуть, пока
        # клиент писал комментарий, поэтому идём общим путём.
        data = await state.get_data()
        day_iso = data.get("day")
        if day_iso:
            # Хелпер сам отвечает на колбэк — выходим, чтобы не
            # продублировать answer() в конце функции.
            await _show_slots_or_back(
                callback, message, state, schedule, date.fromisoformat(day_iso)
            )
            return
        else:
            # Время вводилось текстом (слотов не было) — возвращаем туда же.
            await state.set_state(OrderForm.time_freeform)
            await _prompt_via_edit(message, state, NO_SLOTS_TEXT, text_step_keyboard())
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


@router.callback_query(F.data == ORDER_CONFIRM, StateFilter(OrderForm.confirm))
async def confirm_order(
    callback: CallbackQuery,
    state: FSMContext,
    catalog: Catalog,
    orders: OrderRepository,
    notifier: AdminNotifier,
) -> None:
    """Клиент подтвердил заявку — сохраняем её в базу.

    Сохранение идёт ПЕРВЫМ действием, до любых внешних вызовов.
    Выгрузка в Google Sheets (Фаза 6) и уведомление админам (Фаза 7)
    будут работать уже с сохранённой записью, поэтому их падение
    не может привести к потере заявки.
    """
    message = _require_message(callback)
    data = await state.get_data()

    # Снимок направления на момент заявки: если позже его переименуют
    # или изменят цену, заявка сохранит то, что видел клиент.
    direction = catalog.get_direction(data["direction_id"])

    new_order = NewOrder(
        telegram_user_id=callback.from_user.id,
        telegram_username=callback.from_user.username,
        direction_id=data["direction_id"],
        direction_title=direction.title if direction else data["direction_id"],
        price_per_lesson=direction.price_per_lesson if direction else 0,
        for_whom=data["for_whom"],
        name=data["name"],
        phone=data["phone"],
        preferred_time=data["time"],
        comment=data.get("comment", ""),
    )

    try:
        order = await orders.create(new_order)
    except Exception:
        # Локальная база упасть почти не может, но если это случилось —
        # НЕ говорим клиенту «готово». Состояние не сбрасываем: данные
        # анкеты остаются в FSM, и человек может нажать «Подтвердить»
        # ещё раз, ничего не вводя заново.
        logger.exception("Не удалось сохранить заявку")
        await callback.answer(
            "Не удалось сохранить заявку. Попробуйте нажать «Подтвердить» ещё раз.",
            show_alert=True,
        )
        return

    await state.clear()
    if message is not None:
        await message.edit_text(
            f"✅ Заявка №{order.id} принята!\n\n"
            f"{order.direction_title} — {FOR_WHOM_LABELS.get(order.for_whom, order.for_whom)}\n"
            f"Мы позвоним на {format_phone_display(order.phone)}, "
            "чтобы подтвердить время.\n\n"
            "Спасибо! 🎵"
        )
    await callback.answer("Готово!")

    # Уведомляем администраторов ПОСЛЕ ответа клиенту: он уже получил
    # подтверждение и не ждёт. Метод сам гасит свои ошибки, поэтому
    # выкинутый из чата бот не превратится в сбой для клиента.
    await notifier.notify_new_order(order)


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


@router.callback_query(DayCallback.filter())
async def stale_day(callback: CallbackQuery) -> None:
    await callback.answer(STALE_TEXT, show_alert=True)


@router.callback_query(SlotCallback.filter())
async def stale_slot(callback: CallbackQuery) -> None:
    await callback.answer(STALE_TEXT, show_alert=True)
