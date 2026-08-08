"""Тесты сервиса расписания.

Ключевой приём: «сейчас» всегда передаётся параметром, а не берётся
из datetime.now(). Тест, зависящий от реального времени, однажды упадёт
в полночь, в воскресенье или при переводе часов — и будет падать
случайным образом. Такие «мигающие» тесты хуже отсутствующих:
им перестают верить и в итоге отключают.
"""

from datetime import date, datetime, time

import pytest

from bot.catalog import Schedule
from bot.services.schedule import ScheduleService
from tests.conftest import FRIDAY_NOON, MOSCOW


@pytest.fixture
def service(schedule: Schedule) -> ScheduleService:
    return ScheduleService(schedule, MOSCOW)


class TestAvailableSlots:
    def test_future_day_returns_all_slots(self, service: ScheduleService) -> None:
        """Для будущей даты отсекать нечего."""
        next_monday = date(2026, 8, 10)
        assert len(service.available_slots(next_monday, FRIDAY_NOON)) == 8

    def test_past_day_is_empty(self, service: ScheduleService) -> None:
        assert service.available_slots(date(2026, 8, 1), FRIDAY_NOON) == []

    def test_day_off_is_empty(self, service: ScheduleService) -> None:
        """9 августа 2026 — воскресенье, в расписании его нет."""
        assert service.available_slots(date(2026, 8, 9), FRIDAY_NOON) == []

    def test_today_cuts_past_slots(self, service: ScheduleService) -> None:
        """В пятницу в 12:00 первый доступный слот — 14:00 (буфер 2 ч)."""
        slots = service.available_slots(FRIDAY_NOON.date(), FRIDAY_NOON)
        assert slots[0] == time(14, 0)

    @pytest.mark.parametrize(
        "now_hour,now_minute,expected_first",
        [
            (11, 59, time(14, 0)),
            (12, 0, time(14, 0)),  # ровно 2 часа — слот ещё доступен
            (12, 1, time(15, 0)),  # минутой позже — уже нет
        ],
    )
    def test_buffer_boundary_is_exact(
        self,
        service: ScheduleService,
        now_hour: int,
        now_minute: int,
        expected_first: time,
    ) -> None:
        """Граница буфера считается точно, а не «примерно по часам»."""
        now = datetime(2026, 8, 7, now_hour, now_minute, tzinfo=MOSCOW)
        assert service.available_slots(now.date(), now)[0] == expected_first

    def test_late_evening_leaves_nothing_today(self, service: ScheduleService) -> None:
        """После 18:00 записаться на сегодня уже нельзя."""
        late = datetime(2026, 8, 7, 18, 0, tzinfo=MOSCOW)
        assert service.available_slots(late.date(), late) == []


class TestAvailableDays:
    def test_skips_days_off(self, service: ScheduleService) -> None:
        days = service.available_days(FRIDAY_NOON)
        assert all(d.weekday() != 6 for d in days), "воскресенья не должно быть"

    def test_respects_horizon(self, service: ScheduleService) -> None:
        days = service.available_days(FRIDAY_NOON)
        span = (days[-1] - FRIDAY_NOON.date()).days
        assert span < 14

    def test_starts_from_today_when_slots_left(self, service: ScheduleService) -> None:
        days = service.available_days(FRIDAY_NOON)
        assert days[0] == FRIDAY_NOON.date()

    def test_skips_today_when_no_slots_left(self, service: ScheduleService) -> None:
        """Поздно вечером сегодняшний день выпадает из списка."""
        late = datetime(2026, 8, 7, 22, 0, tzinfo=MOSCOW)
        assert service.available_days(late)[0] > late.date()

    def test_empty_when_nothing_fits_horizon(self) -> None:
        """Пустой горизонт включает запасной путь со свободным вводом.

        Расписание только на воскресенье, горизонт 1 день, «сегодня»
        пятница — записаться некуда.
        """
        only_sunday = Schedule.model_validate(
            {
                "slot_minutes": 60,
                "booking_horizon_days": 1,
                "week": {"sun": ["10:00-18:00"]},
            }
        )
        service = ScheduleService(only_sunday, MOSCOW)
        assert service.available_days(FRIDAY_NOON) == []


class TestSlotVerification:
    """Данные из callback_data — внешний ввод, доверять им нельзя."""

    def test_accepts_real_slot(self, service: ScheduleService) -> None:
        assert service.is_slot_available(date(2026, 8, 10), time(12, 0), FRIDAY_NOON)

    @pytest.mark.parametrize(
        "slot,why",
        [
            (time(3, 0), "вне рабочих часов"),
            (time(23, 0), "после закрытия"),
            (time(10, 30), "не по сетке"),
            (time(20, 0), "время закрытия, занятие не начинается"),
        ],
    )
    def test_rejects_forged_slot(
        self, service: ScheduleService, slot: time, why: str
    ) -> None:
        assert not service.is_slot_available(date(2026, 8, 10), slot, FRIDAY_NOON), why

    def test_rejects_slot_in_past(self, service: ScheduleService) -> None:
        """Клиент нажал кнопку из сообщения, пролежавшего час."""
        assert not service.is_slot_available(
            FRIDAY_NOON.date(), time(10, 0), FRIDAY_NOON
        )

    def test_rejects_slot_on_day_off(self, service: ScheduleService) -> None:
        assert not service.is_slot_available(
            date(2026, 8, 9), time(12, 0), FRIDAY_NOON
        )
