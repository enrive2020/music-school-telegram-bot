"""Загрузка каталога направлений из YAML в типизированные модели.

Принцип тот же, что в settings.py: файл проверяется ЦЕЛИКОМ на старте.
Владелец школы опечатался в конфиге → бот отказывается запускаться
и говорит, где ошибка. Плохая альтернатива — узнать об опечатке,
когда клиент нажмёт на кнопку и получит белиберду.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


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


class SchoolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    currency: str = "₽"
    # Двухбуквенный код страны (ISO 3166-1 alpha-2) для разбора
    # телефонов, введённых без кода страны.
    phone_region: str = Field(default="RU", pattern=r"^[A-Z]{2}$")


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
