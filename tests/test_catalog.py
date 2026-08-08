"""Тесты конфига: направления и расписание.

Главная ценность этих тестов — проверка, что бот ОТКАЗЫВАЕТСЯ
стартовать на кривом конфиге. Молча проглоченная опечатка владельца
хуже падения: заявки поедут с неверными данными.
"""

import pytest
from pydantic import ValidationError as PydanticError

from bot.catalog import Catalog, CatalogError, Schedule, TimeInterval, load_catalog


class TestDirections:
    def test_loads_real_config(self) -> None:
        """Боевой config/school.yaml должен быть валиден.

        Единственный тест, который нарочно читает настоящий файл:
        он ловит ситуацию «поправили конфиг и сломали запуск».
        """
        catalog = load_catalog("config/school.yaml")
        assert catalog.directions
        assert catalog.school.name

    def test_button_label_combines_emoji_and_title(self, catalog: Catalog) -> None:
        guitar = catalog.get_direction("guitar")
        assert guitar.button_label == "🎸 Гитара"

    def test_button_label_without_emoji(self, catalog: Catalog) -> None:
        """Эмодзи необязательно — лишнего пробела быть не должно."""
        assert catalog.get_direction("vocal").button_label == "Вокал"

    def test_unknown_direction_returns_none(self, catalog: Catalog) -> None:
        """Кнопка из старого сообщения может ссылаться на удалённое
        направление — код обязан это пережить."""
        assert catalog.get_direction("theremin") is None

    def test_rejects_duplicate_ids(self) -> None:
        """Два направления с одним id = кнопки-близнецы."""
        with pytest.raises(PydanticError, match="дважды"):
            Catalog.model_validate(
                {
                    "school": {"name": "Ш", "schedule": _minimal_schedule()},
                    "directions": [
                        {"id": "guitar", "title": "А", "price_per_lesson": 1,
                         "lesson_minutes": 60},
                        {"id": "guitar", "title": "Б", "price_per_lesson": 1,
                         "lesson_minutes": 60},
                    ],
                }
            )

    def test_rejects_typo_in_field_name(self) -> None:
        """extra=forbid: «prise» вместо «price» уронит запуск.

        Без этого владелец «поменял цену», а бот показывает старую.
        """
        with pytest.raises(PydanticError):
            Catalog.model_validate(
                {
                    "school": {"name": "Ш", "schedule": _minimal_schedule()},
                    "directions": [
                        {"id": "guitar", "title": "А", "prise_per_lesson": 1200,
                         "lesson_minutes": 60},
                    ],
                }
            )

    def test_missing_file_gives_clear_error(self) -> None:
        with pytest.raises(CatalogError, match="не найден"):
            load_catalog("config/нет-такого.yaml")


class TestTimeInterval:
    def test_parses_compact_string(self) -> None:
        """Владелец пишет «12:00-20:00», а не {start: …, end: …}."""
        interval = TimeInterval.model_validate("12:00-20:00")
        assert (interval.start.hour, interval.end.hour) == (12, 20)

    def test_tolerates_spaces(self) -> None:
        assert TimeInterval.model_validate(" 12:00 - 20:00 ").start.hour == 12

    @pytest.mark.parametrize(
        "value", ["12:00 до 20:00", "12:00", "25:00-26:00", "20:00-12:00"]
    )
    def test_rejects_broken(self, value: str) -> None:
        with pytest.raises(PydanticError):
            TimeInterval.model_validate(value)


class TestSchedule:
    def test_generates_slots_from_interval(self, schedule: Schedule) -> None:
        """8 слотов из «12:00-20:00» при шаге 60."""
        slots = schedule.slots_for_weekday("mon")
        assert len(slots) == 8
        assert slots[0].hour == 12
        # Полуоткрытый интервал: 20:00 — время закрытия, занятие
        # в 20:00 уже не начинается.
        assert slots[-1].hour == 19

    def test_missing_day_means_day_off(self, schedule: Schedule) -> None:
        """Воскресенья нет в конфиге — значит выходной."""
        assert schedule.slots_for_weekday("sun") == []

    def test_supports_lunch_break(self) -> None:
        """Перерыв описывается двумя интервалами."""
        sch = Schedule.model_validate(
            {"slot_minutes": 60, "week": {"mon": ["10:00-13:00", "15:00-18:00"]}}
        )
        hours = [s.hour for s in sch.slots_for_weekday("mon")]
        assert hours == [10, 11, 12, 15, 16, 17]  # 13 и 14 выпали

    def test_rejects_overlapping_intervals(self) -> None:
        with pytest.raises(PydanticError, match="пересекаются"):
            Schedule.model_validate(
                {"slot_minutes": 60, "week": {"mon": ["10:00-14:00", "13:00-18:00"]}}
            )

    def test_rejects_interval_not_divisible_by_step(self) -> None:
        """Иначе последний слот «свисает» за время закрытия."""
        with pytest.raises(PydanticError, match="не делится"):
            Schedule.model_validate(
                {"slot_minutes": 45, "week": {"mon": ["10:00-14:00"]}}
            )

    def test_rejects_empty_day_list(self) -> None:
        """Пустой список двусмыслен: выходной или забыли дописать?"""
        with pytest.raises(PydanticError, match="без интервалов"):
            Schedule.model_validate({"slot_minutes": 60, "week": {"mon": []}})

    def test_rejects_empty_week(self) -> None:
        with pytest.raises(PydanticError, match="ни одного рабочего дня"):
            Schedule.model_validate({"slot_minutes": 60, "week": {}})

    def test_rejects_unknown_weekday(self) -> None:
        with pytest.raises(PydanticError):
            Schedule.model_validate(
                {"slot_minutes": 60, "week": {"monday": ["12:00-20:00"]}}
            )


class TestTimezone:
    def test_rejects_unknown_timezone(self) -> None:
        """Опечатка «Europe/Moskow» иначе всплыла бы при первой заявке."""
        with pytest.raises(PydanticError, match="часовой пояс"):
            Catalog.model_validate(
                {
                    "school": {
                        "name": "Ш",
                        "timezone": "Europe/Moskow",
                        "schedule": _minimal_schedule(),
                    },
                    "directions": [
                        {"id": "a", "title": "А", "price_per_lesson": 1,
                         "lesson_minutes": 60}
                    ],
                }
            )


def _minimal_schedule() -> dict:
    """Расписание-заглушка для тестов, где проверяется не оно."""
    return {"slot_minutes": 60, "week": {"mon": ["12:00-20:00"]}}
