"""Общие фикстуры для тестов.

conftest.py — специальный файл: pytest подхватывает его сам, ничего
импортировать не нужно. Всё, что объявлено здесь через @pytest.fixture,
доступно любому тесту в этой папке и вложенных.

Фикстура — это подготовленный объект, который тест запрашивает,
объявив параметр с таким же именем. pytest создаёт его перед тестом
и убирает после. Так тесты не копируют один и тот же setup.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from bot.catalog import Catalog, Schedule

MOSCOW = ZoneInfo("Europe/Moscow")

# 7 августа 2026 — пятница. Фиксированная дата, а не datetime.now():
# тест, зависящий от текущего времени, однажды упадёт в полночь или
# в выходной. Такие тесты («мигающие») хуже отсутствующих — им
# перестают верить.
FRIDAY_NOON = datetime(2026, 8, 7, 12, 0, tzinfo=MOSCOW)


@pytest.fixture
def schedule() -> Schedule:
    """Расписание как в боевом конфиге: будни 12–20, суббота 10–18."""
    return Schedule.model_validate(
        {
            "slot_minutes": 60,
            "booking_horizon_days": 14,
            "booking_buffer_hours": 2,
            "week": {
                "mon": ["12:00-20:00"],
                "tue": ["12:00-20:00"],
                "wed": ["12:00-20:00"],
                "thu": ["12:00-20:00"],
                "fri": ["12:00-20:00"],
                "sat": ["10:00-18:00"],
            },
        }
    )


@pytest.fixture
def catalog(schedule: Schedule) -> Catalog:
    """Минимальный каталог для тестов.

    Обрати внимание: фикстура собирает каталог из словаря, а не читает
    config/school.yaml. Тест не должен зависеть от боевого конфига —
    иначе смена цены гитары уронит половину тестов.
    """
    return Catalog.model_validate(
        {
            "school": {
                "name": "Тестовая школа",
                "currency": "₽",
                "phone_region": "RU",
                "timezone": "Europe/Moscow",
                "schedule": schedule.model_dump(mode="json"),
            },
            "directions": [
                {
                    "id": "guitar",
                    "title": "Гитара",
                    "emoji": "🎸",
                    "price_per_lesson": 1200,
                    "lesson_minutes": 60,
                },
                {
                    "id": "vocal",
                    "title": "Вокал",
                    "price_per_lesson": 1500,
                    "lesson_minutes": 45,
                },
            ],
        }
    )
