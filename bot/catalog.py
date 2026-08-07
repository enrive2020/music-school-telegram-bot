"""Загрузка каталога направлений из YAML в типизированные модели.

Принцип тот же, что в settings.py: файл проверяется ЦЕЛИКОМ на старте.
Владелец школы опечатался в конфиге → бот отказывается запускаться
и говорит, где ошибка. Плохая альтернатива — узнать об опечатке,
когда клиент нажмёт на кнопку и получит белиберду.
"""

from datetime import time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

# Ключи дней недели. Порядок совпадает с date.weekday() (0 = понедельник),
# поэтому по индексу из даты сразу получаем нужный ключ конфига.
WEEKDAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _to_minutes(t: time) -> int:
    """Время суток → минуты от полуночи. Для арифметики над сеткой."""
    return t.hour * 60 + t.minute


def _from_minutes(total: int) -> time:
    """Минуты от полуночи → время суток."""
    return time(total // 60, total % 60)


class CatalogError(Exception):
    """Ошибка конфига каталога — с человекочитаемым текстом для лога."""


class Direction(BaseModel):
    """Одно направление обучения."""

    # extra="forbid": неизвестный ключ в YAML — это почти наверняка
    # опечатка (например, "prise" вместо "price"). Молча игнорировать
    # её нельзя: владелец будет уверен, что поменял цену, а бот
    # покажет старую. Лучше упасть и показать, где опечатка.
    model_config = ConfigDict(extra="forbid")

    # id уходит в callback_data кнопок (лимит Telegram — 64 байта)
    # и в сохранённые заявки, поэтому: латиница, коротко, уникально.
    id: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    title: str = Field(min_length=1, max_length=64)
    emoji: str = ""
    description: str = Field(default="", max_length=500)
    price_per_lesson: int = Field(ge=0)
    lesson_minutes: int = Field(gt=0, le=480)

    @property
    def button_label(self) -> str:
        """Текст для кнопки: «🎸 Гитара» или просто «Гитара»."""
        return f"{self.emoji} {self.title}".strip()


class TimeInterval(BaseModel):
    """Рабочий интервал одного дня. Полуоткрытый: [start, end)."""

    model_config = ConfigDict(extra="forbid")

    start: time
    end: time

    @model_validator(mode="before")
    @classmethod
    def _parse_compact(cls, data: object) -> object:
        """Позволяет писать «12:00-20:00» вместо {start: …, end: …}.

        Конфиг правит владелец школы, а не программист: компактная
        строка читается легче вложенного объекта.
        """
        if isinstance(data, str):
            parts = data.split("-")
            if len(parts) != 2:
                raise ValueError(
                    f"интервал '{data}' должен быть в виде «12:00-20:00»"
                )
            return {"start": parts[0].strip(), "end": parts[1].strip()}
        return data

    @model_validator(mode="after")
    def _start_before_end(self) -> "TimeInterval":
        # Некорректное время («25:00») pydantic отсеет сам при разборе time.
        if self.start >= self.end:
            raise ValueError(
                f"конец интервала ({self.end:%H:%M}) должен быть позже "
                f"начала ({self.start:%H:%M})"
            )
        return self


class Schedule(BaseModel):
    """Недельный ШАБЛОН доступного времени.

    Здесь только «какие слоты бывают в принципе». Занятость конкретных
    дат — это состояние во времени, ему место в базе, а не в конфиге.
    Разделение позволит добавить занятые слоты, не трогая эту схему.
    """

    model_config = ConfigDict(extra="forbid")

    slot_minutes: int = Field(gt=0, le=240)
    booking_horizon_days: int = Field(default=14, gt=0, le=90)
    booking_buffer_hours: int = Field(default=2, ge=0, le=72)
    # Дня нет в словаре → выходной.
    week: dict[Weekday, list[TimeInterval]]

    @field_validator("week")
    @classmethod
    def _week_usable(cls, week: dict) -> dict:
        if not week:
            raise ValueError(
                "в расписании нет ни одного рабочего дня — записаться некуда"
            )
        for day, intervals in week.items():
            if not intervals:
                # Пустой список двусмыслен: выходной обозначается
                # отсутствием ключа, а не пустым значением.
                raise ValueError(
                    f"день '{day}' указан без интервалов. "
                    "Для выходного удалите строку этого дня целиком"
                )
        return week

    @model_validator(mode="after")
    def _intervals_consistent(self) -> "Schedule":
        for day, intervals in self.week.items():
            ordered = sorted(intervals, key=lambda iv: iv.start)

            # Пересечения: «10:00-14:00» и «13:00-18:00» дали бы
            # дублирующиеся слоты в сетке.
            for prev, cur in zip(ordered, ordered[1:]):
                if cur.start < prev.end:
                    raise ValueError(
                        f"в '{day}' интервалы пересекаются: "
                        f"{prev.start:%H:%M}-{prev.end:%H:%M} и "
                        f"{cur.start:%H:%M}-{cur.end:%H:%M}"
                    )

            # Кратность шагу: иначе последний слот «свисает» за время
            # закрытия. Например 10:00-14:00 при шаге 45 даёт последний
            # слот в 13:45, а занятие закончится в 14:30.
            for iv in intervals:
                length = _to_minutes(iv.end) - _to_minutes(iv.start)
                if length % self.slot_minutes != 0:
                    raise ValueError(
                        f"в '{day}' интервал {iv.start:%H:%M}-{iv.end:%H:%M} "
                        f"({length} мин) не делится нацело на шаг "
                        f"{self.slot_minutes} мин — последний слот выйдет "
                        "за время закрытия"
                    )
        return self

    def slots_for_weekday(self, weekday_key: str) -> list[time]:
        """Все слоты указанного дня недели, по возрастанию."""
        slots: list[time] = []
        for iv in sorted(self.week.get(weekday_key, []), key=lambda x: x.start):
            current = _to_minutes(iv.start)
            end = _to_minutes(iv.end)
            while current < end:  # полуоткрыто: end не входит
                slots.append(_from_minutes(current))
                current += self.slot_minutes
        return slots


class SchoolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    currency: str = "₽"
    # Двухбуквенный код страны (ISO 3166-1 alpha-2) для разбора
    # телефонов, введённых без кода страны.
    phone_region: str = Field(default="RU", pattern=r"^[A-Z]{2}$")

    # Часовой пояс школы: в базе время хранится в UTC, а владельцу
    # в таблице и уведомлениях показывается местное.
    timezone: str = "Europe/Moscow"

    @field_validator("timezone")
    @classmethod
    def _tz_exists(cls, value: str) -> str:
        # Проверяем на старте: опечатка «Europe/Moskow» иначе всплыла бы
        # только при первой выгрузке заявки.
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError(
                f"неизвестный часовой пояс '{value}'. "
                "Примеры: Europe/Moscow, Asia/Almaty, Europe/Minsk"
            ) from None
        return value

    # Расписание обязательно: без него записаться не на что.
    schedule: Schedule

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class Catalog(BaseModel):
    """Корень конфига: информация о школе + список направлений."""

    model_config = ConfigDict(extra="forbid")

    school: SchoolInfo
    directions: list[Direction] = Field(min_length=1)

    @field_validator("directions")
    @classmethod
    def _ids_unique(cls, dirs: list[Direction]) -> list[Direction]:
        # Два направления с одним id = кнопки-близнецы, ведущие
        # в одно место. Pydantic сам это не поймает — проверяем руками.
        seen: set[str] = set()
        for d in dirs:
            if d.id in seen:
                raise ValueError(f"направление с id='{d.id}' встречается дважды")
            seen.add(d.id)
        return dirs

    def get_direction(self, direction_id: str) -> Direction | None:
        """Найти направление по id (None — если из старой кнопки пришёл id, которого больше нет)."""
        for d in self.directions:
            if d.id == direction_id:
                return d
        return None


def load_catalog(path: str | Path) -> Catalog:
    """Читает YAML и возвращает провалидированный каталог.

    Любая проблема — файла нет, YAML кривой, данные не проходят
    проверку — превращается в CatalogError с понятным текстом.
    """
    file = Path(path)
    if not file.exists():
        raise CatalogError(f"Файл каталога не найден: {file.resolve()}")

    try:
        # safe_load, а не load: load умеет создавать произвольные
        # python-объекты из YAML-тегов — это дыра в безопасности,
        # если файл когда-нибудь придёт из недоверенного источника.
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CatalogError(f"Не удалось разобрать YAML в {file}:\n{e}") from e

    try:
        return Catalog.model_validate(raw)
    except ValidationError as e:
        # Ошибки pydantic сами по себе информативны (путь до поля +
        # причина) — добавляем только контекст, какой файл смотреть.
        raise CatalogError(f"Каталог {file} заполнен с ошибками:\n{e}") from e
