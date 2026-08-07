"""Тесты форматирования дат и кодирования времени для кнопок."""

from datetime import date, time

import pytest

from bot.formatters import (
    format_day_button,
    format_day_full,
    format_slot,
    hm_to_time,
    time_to_hm,
)

# 9 августа 2026 — воскресенье, 10 августа — понедельник.
SUNDAY = date(2026, 8, 9)
MONDAY = date(2026, 8, 10)


class TestDateFormatting:
    def test_button_label_is_compact(self) -> None:
        """Кнопок две в ряд — подпись должна быть короткой."""
        assert format_day_button(MONDAY) == "10 авг, пн"

    def test_full_label_for_header(self) -> None:
        assert format_day_full(SUNDAY) == "9 августа, воскресенье"

    def test_slot_label_for_order(self) -> None:
        """Эта строка уезжает владельцу в таблицу и уведомление."""
        assert format_slot(MONDAY, time(18, 0)) == "10 августа (пн), 18:00"

    def test_uses_genitive_case(self) -> None:
        """«9 августа», а не «9 август» — иначе выглядит нелепо."""
        assert "августа" in format_day_full(SUNDAY)

    def test_month_names_are_hardcoded_not_locale(self) -> None:
        """Регрессия против strftime('%B').

        Формат даты зависит от локали ОС: на сервере с английской
        локалью клиент увидел бы «9 August». Таблица делает вывод
        одинаковым где угодно.
        """
        assert "August" not in format_day_full(SUNDAY)
        assert "Sun" not in format_day_button(SUNDAY)

    @pytest.mark.parametrize(
        "day,expected_weekday",
        [
            (date(2026, 8, 10), "пн"),
            (date(2026, 8, 11), "вт"),
            (date(2026, 8, 15), "сб"),
            (date(2026, 8, 16), "вс"),
        ],
    )
    def test_weekday_matches_calendar(self, day: date, expected_weekday: str) -> None:
        assert format_day_button(day).endswith(expected_weekday)


class TestTimeEncoding:
    def test_encodes_without_colon(self) -> None:
        """Двоеточие внутри callback_data ломает разбор полей:
        CallbackData использует его как разделитель."""
        encoded = time_to_hm(time(18, 0))
        assert encoded == "1800"
        assert ":" not in encoded

    def test_pads_single_digits(self) -> None:
        assert time_to_hm(time(9, 5)) == "0905"

    def test_roundtrip(self) -> None:
        for slot in [time(0, 0), time(9, 30), time(18, 0), time(23, 59)]:
            assert hm_to_time(time_to_hm(slot)) == slot

    @pytest.mark.parametrize(
        "value", ["", "18:0", "180", "18000", "abcd", "9999", "2500", "1860"]
    )
    def test_broken_input_returns_none(self, value: str) -> None:
        """Данные из callback_data — внешний ввод.

        Возвращаем None, а не исключение: вызывающий код должен
        показать клиенту понятное сообщение, а не упасть.
        """
        assert hm_to_time(value) is None
