"""Тесты валидации пользовательского ввода.

Про @pytest.mark.parametrize: вместо цикла внутри одного теста
pytest создаёт ОТДЕЛЬНЫЙ тест на каждое значение. Если упадёт третий
случай, в отчёте будет видно именно его — а не «тест имени сломался».
"""

import pytest

from bot.validators import (
    ValidationError,
    format_phone_display,
    validate_comment,
    validate_name,
    validate_phone,
    validate_time,
)

# «Андрей» с разложенным «й»: буква «и» + комбинирующий знак краткости.
# Именно в таком виде имя приходит с части клиентов iOS/macOS.
DECOMPOSED_ANDREY = "Андре" + "и" + "̆"


class TestName:
    """Валидация имени.

    Тесты сгруппированы в класс просто для читаемости отчёта —
    наследовать ничего не нужно, это обычный класс.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "Анна",
            "Ли",
            "Анна Мария",
            "Анна П.",
            "И. Иванов",
            "Жан-Клод",
            "О'Коннор",
            "О’Коннор",  # типографский апостроф
            "Мария-Тереза Ф.",
            "John Smith",
            "Алла",  # удвоение — не спам
            "Жанна",
            "Филипп",
            "Aaltonen",
        ],
    )
    def test_accepts_real_names(self, value: str) -> None:
        assert validate_name(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "A",  # одна буква
            "123",
            "g%G%",  # баг, найденный вручную в Фазе 4
            "Анна2",
            "Анна🎸",
            "🎸🎸🎸",
            "Анна@mail.ru",
            "https://spam.ru",
            ".Анна",  # начинается со знака
            "-Анна",
            "Анна-",  # кончается знаком
            "Ан..",  # два знака подряд
            "А . Б",
            "Жан--Клод",
            "А" * 61,  # длиннее лимита
        ],
    )
    def test_rejects_garbage(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_name(value)

    @pytest.mark.parametrize("value", ["Ааааа", "ААА", "АаА", "ыыыыы"])
    def test_rejects_keyboard_spam(self, value: str) -> None:
        """Три одинаковые буквы подряд — не имя."""
        with pytest.raises(ValidationError):
            validate_name(value)

    @pytest.mark.parametrize("value", ["А.Б.", "А-Б", "И.И."])
    def test_rejects_without_real_word(self, value: str) -> None:
        """Нужен непрерывный отрезок из 2+ букв.

        «И.И.» отклоняется сознательно: размен согласован при
        проектировании, см. docs/design-name-and-slots.md.
        """
        with pytest.raises(ValidationError):
            validate_name(value)

    def test_normalizes_decomposed_letters(self) -> None:
        """Разложенный «й» с iOS должен приниматься.

        Регрессия на реальный дефект: до NFC-нормализации имя
        «Андрей» с такого клиента получало отказ.
        """
        assert validate_name(DECOMPOSED_ANDREY) == "Андрей"
        # И результат совпадает с обычным написанием — важно, иначе
        # один человек попадёт в таблицу как два разных.
        assert validate_name(DECOMPOSED_ANDREY) == validate_name("Андрей")

    def test_collapses_spaces_and_strips_invisibles(self) -> None:
        # Zero-width space внутри имени — приём обхода фильтров.
        assert validate_name("  Анна​   П.  ") == "Анна П."

    def test_error_message_is_for_humans(self) -> None:
        """Текст ошибки показывается КЛИЕНТУ — он должен объяснять.

        Проверяем свойства сообщения, а не точную формулировку:
        привязка к словоформе сделала бы тест хрупким — он краснел бы
        при любой правке текста, ничего при этом не поймав.
        """
        with pytest.raises(ValidationError) as exc:
            validate_name("Анна2")
        message = str(exc.value)

        # Сообщение развёрнутое, а не «ошибка».
        assert len(message) > 30
        # И содержит пример правильного ввода — клиент должен понять,
        # что от него хотят, без второй попытки наугад.
        assert "«" in message


class TestPhone:
    @pytest.mark.parametrize(
        "value",
        [
            "+79001234567",
            "89001234567",
            "8 (900) 123-45-67",
            "9001234567",
            "+7 900 123 45 67",
            "+7-900-123-45-67",
            "  +7 900 123 45 67  ",
        ],
    )
    def test_normalizes_to_e164(self, value: str) -> None:
        """Любой формат записи даёт одну и ту же строку.

        Иначе один клиент, записавшийся дважды по-разному,
        выглядит в таблице как два разных человека.
        """
        assert validate_phone(value) == "+79001234567"

    @pytest.mark.parametrize(
        "value,region",
        [("+375291234567", "RU"), ("+77011234567", "RU"), ("291234567", "BY")],
    )
    def test_accepts_other_countries(self, value: str, region: str) -> None:
        assert validate_phone(value, region).startswith("+")

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "абырвалг",
            "123",
            "+7900123456",  # на цифру короче
            "89991234567890",  # длиннее нормы
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_phone(value)

    def test_display_format_is_readable(self) -> None:
        assert format_phone_display("+79001234567") == "+7 900 123-45-67"

    def test_display_survives_garbage(self) -> None:
        """Форматирование не должно падать: показать как есть лучше."""
        assert format_phone_display("не номер") == "не номер"


class TestTime:
    """Свободный ввод времени — запасной путь, когда слотов нет."""

    @pytest.mark.parametrize(
        "value", ["будни после 18:00", "сб утром", "19", "в любое время"]
    )
    def test_accepts_reasonable(self, value: str) -> None:
        assert validate_time(value) == value

    @pytest.mark.parametrize("value", ["", "!", "х" * 101])
    def test_rejects_garbage(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_time(value)


class TestComment:
    def test_empty_is_allowed(self) -> None:
        """Комментарий необязателен."""
        assert validate_comment("") == ""

    def test_accepts_normal(self) -> None:
        assert validate_comment("Ребёнку 7 лет") == "Ребёнку 7 лет"

    def test_rejects_too_long(self) -> None:
        with pytest.raises(ValidationError):
            validate_comment("я" * 501)
