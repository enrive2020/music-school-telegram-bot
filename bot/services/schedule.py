"""Доступные для записи дни и слоты.

Превращает недельный ШАБЛОН из конфига в конкретные даты и времена
с учётом «сейчас»: сегодняшние слоты, до которых осталось меньше
буфера, уже не показываются.

Это ЕДИНСТВЕННАЯ точка, где рождается список доступных слотов.
Благодаря этому будущая проверка занятости (какие слоты уже забронированы
по данным базы) встанет сюда же — обработчики диалога менять не придётся.

Про часовые пояса: «сейчас» считаем в поясе школы, сравнение делаем на
datetime с таймзоной. Известное допущение — сетка не учитывает переходы
на летнее время; для Europe/Moscow (переходов нет с 2014) это безопасно,
для поясов с DST в дни перевода часов возможен сдвиг на час.
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot.catalog import WEEKDAY_KEYS, Schedule

logger = logging.getLogger(__name__)


class ScheduleService:
    """Считает, на какие дни и часы можно записаться."""

    def __init__(self, schedule: Schedule, tz: ZoneInfo) -> None:
        self._schedule = schedule
        self._tz = tz

    def now(self) -> datetime:
        """Текущий момент в поясе школы."""
        return datetime.now(self._tz)

    def _slot_datetime(self, day: date, slot: time) -> datetime:
        """Слот как момент времени в поясе школы — для сравнения с «сейчас»."""
        return datetime.combine(day, slot, tzinfo=self._tz)

    def available_slots(self, day: date, now: datetime | None = None) -> list[time]:
        """Свободные слоты указанной даты.

        Пустой список означает «в этот день записаться нельзя» — либо
        выходной, либо все слоты уже прошли.
        """
        moment = now or self.now()

        # Ключ дня недели: date.weekday() даёт 0 для понедельника,
        # и WEEKDAY_KEYS выстроен в том же порядке.
        weekday_key = WEEKDAY_KEYS[day.weekday()]
        slots = self._schedule.slots_for_weekday(weekday_key)

        # Прошлые даты недоступны целиком.
        if day < moment.date():
            return []

        # Для будущих дат отсекать нечего.
        if day > moment.date():
            return slots

        # Сегодня: убираем слоты, до которых осталось меньше буфера.
        # Запас нужен, чтобы администратор успел перезвонить.
        cutoff = moment + timedelta(hours=self._schedule.booking_buffer_hours)
        return [s for s in slots if self._slot_datetime(day, s) >= cutoff]

    def available_days(self, now: datetime | None = None) -> list[date]:
        """Даты в пределах горизонта, где есть хотя бы один свободный слот."""
        moment = now or self.now()
        today = moment.date()

        days: list[date] = []
        for offset in range(self._schedule.booking_horizon_days):
            day = today + timedelta(days=offset)
            if self.available_slots(day, moment):
                days.append(day)
        return days

    def is_slot_available(
        self, day: date, slot: time, now: datetime | None = None
    ) -> bool:
        """Можно ли ещё записаться на этот слот.

        Нужна при обработке нажатия: кнопка могла быть нажата из старого
        сообщения, а слот к этому моменту уже прошёл. Данным из callback
        доверять нельзя — сверяемся с актуальным расписанием.
        """
        return slot in self.available_slots(day, now)
