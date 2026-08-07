"""Модель заявки — то, что мы храним в базе.

Важное решение: заявка хранит СНИМОК данных на момент оформления
(название направления, цена), а не только ссылку на id из конфига.
Владелец школы завтра переименует «Гитару» в «Гитару для начинающих»
или поднимет цену — старые заявки должны помнить, на что и по какой
цене человек записывался. Ссылка на конфиг такого не даёт.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    """Стадия жизни заявки.

    Наследуемся от str, чтобы значение само по себе было строкой:
    удобно писать в базу и сравнивать без .value.
    """

    NEW = "new"          # сохранена локально, в Google Sheets ещё не ушла
    SYNCED = "synced"    # успешно выгружена
    FAILED = "failed"    # выгрузка падала слишком много раз, нужна ручная проверка


def utc_now() -> datetime:
    """Текущее время в UTC.

    Храним всегда в UTC, а показываем людям в их часовом поясе.
    Иначе перевод сервера в другой регион молча сдвинет все даты.
    """
    return datetime.now(timezone.utc)


class NewOrder(BaseModel):
    """Заявка, которую собрал диалог, — ещё без id и статуса."""

    telegram_user_id: int
    telegram_username: str | None = None

    direction_id: str
    direction_title: str          # снимок названия на момент заявки
    price_per_lesson: int         # снимок цены

    for_whom: str                 # 'child' | 'adult'
    name: str
    phone: str                    # в формате E.164
    preferred_time: str
    comment: str = ""


class Order(NewOrder):
    """Заявка, прочитанная из базы: с id, статусом и служебными полями."""

    id: int
    created_at: datetime
    status: OrderStatus = OrderStatus.NEW
    sync_attempts: int = 0        # сколько раз пытались выгрузить
    last_error: str | None = None # текст последней ошибки выгрузки
    synced_at: datetime | None = Field(default=None)
