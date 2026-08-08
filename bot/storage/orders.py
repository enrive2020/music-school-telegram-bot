"""Репозиторий заявок — единственное место в проекте, где есть SQL.

Хендлеры вызывают методы этого класса и ничего не знают о том, что
внутри SQLite. Чтобы завтра переехать на PostgreSQL или в CRM,
достаточно написать класс с такими же методами — диалог не изменится.
"""

import logging
from datetime import datetime

import aiosqlite

from bot.storage.models import NewOrder, Order, OrderStatus, utc_now

logger = logging.getLogger(__name__)

# Сколько раз пробуем выгрузить заявку, прежде чем пометить её FAILED
# и позвать человека. Используется в Фазе 6.
MAX_SYNC_ATTEMPTS = 5


def _row_to_order(row: aiosqlite.Row) -> Order:
    """Превращает строку БД в модель.

    Даты в SQLite лежат текстом — разбираем их обратно в datetime,
    чтобы наружу репозиторий отдавал нормальные питоновские типы.
    """
    synced_at = row["synced_at"]
    return Order(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        telegram_user_id=row["telegram_user_id"],
        telegram_username=row["telegram_username"],
        direction_id=row["direction_id"],
        direction_title=row["direction_title"],
        price_per_lesson=row["price_per_lesson"],
        for_whom=row["for_whom"],
        name=row["name"],
        phone=row["phone"],
        preferred_time=row["preferred_time"],
        comment=row["comment"],
        status=OrderStatus(row["status"]),
        sync_attempts=row["sync_attempts"],
        last_error=row["last_error"],
        synced_at=datetime.fromisoformat(synced_at) if synced_at else None,
    )


class OrderRepository:
    """Доступ к таблице заявок."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(self, order: NewOrder) -> Order:
        """Сохраняет новую заявку и возвращает её уже с id.

        Это первое, что происходит после подтверждения клиентом —
        ДО уведомлений и выгрузки в Google Sheets. Поэтому упавший
        внешний сервис не может привести к потере заявки.
        """
        created_at = utc_now()
        cursor = await self._conn.execute(
            """
            INSERT INTO orders (
                created_at, telegram_user_id, telegram_username,
                direction_id, direction_title, price_per_lesson,
                for_whom, name, phone, preferred_time, comment, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            # Параметры передаём через «?», а не через f-строку:
            # это защита от SQL-инъекций. Клиент может написать в имени
            # что угодно — драйвер экранирует значение сам.
            (
                created_at.isoformat(),
                order.telegram_user_id,
                order.telegram_username,
                order.direction_id,
                order.direction_title,
                order.price_per_lesson,
                order.for_whom,
                order.name,
                order.phone,
                order.preferred_time,
                order.comment,
                OrderStatus.NEW.value,
            ),
        )
        await self._conn.commit()

        order_id = cursor.lastrowid
        logger.info("Заявка #%s сохранена (%s)", order_id, order.direction_title)

        return Order(
            id=order_id,
            created_at=created_at,
            status=OrderStatus.NEW,
            **order.model_dump(),
        )

    async def get(self, order_id: int) -> Order | None:
        async with self._conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_order(row) if row else None

    async def list_pending(self, limit: int = 50) -> list[Order]:
        """Заявки, ожидающие выгрузки, — самые старые первыми.

        Используется фоновой синхронизацией в Фазе 6.
        """
        async with self._conn.execute(
            """
            SELECT * FROM orders
            WHERE status = ? AND sync_attempts < ?
            ORDER BY created_at
            LIMIT ?
            """,
            (OrderStatus.NEW.value, MAX_SYNC_ATTEMPTS, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_order(row) for row in rows]

    async def list_recent(self, limit: int = 10) -> list[Order]:
        """Последние заявки — для админ-команд и отладки."""
        async with self._conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_order(row) for row in rows]

    async def mark_synced(self, order_id: int) -> None:
        """Заявка успешно уехала в Google Sheets."""
        await self._conn.execute(
            "UPDATE orders SET status = ?, synced_at = ?, last_error = NULL WHERE id = ?",
            (OrderStatus.SYNCED.value, utc_now().isoformat(), order_id),
        )
        await self._conn.commit()

    async def mark_sync_failed(self, order_id: int, error: str) -> None:
        """Попытка выгрузки не удалась.

        Счётчик попыток растёт; когда он упрётся в MAX_SYNC_ATTEMPTS,
        заявка получает статус FAILED и перестаёт бесконечно долбиться
        в сломанный сервис — вместо этого о ней сообщат человеку.
        """
        await self._conn.execute(
            """
            UPDATE orders
            SET sync_attempts = sync_attempts + 1,
                last_error = ?,
                status = CASE
                    WHEN sync_attempts + 1 >= ? THEN ?
                    ELSE status
                END
            WHERE id = ?
            """,
            # Текст ошибки подрезаем: полный трейсбек в БД не нужен,
            # он всё равно попадёт в логи.
            (error[:500], MAX_SYNC_ATTEMPTS, OrderStatus.FAILED.value, order_id),
        )
        await self._conn.commit()

    async def count_since(self, moment: datetime) -> int:
        """Сколько заявок создано начиная с указанного момента.

        Даты в базе — ISO-строки в UTC, поэтому сравнение строк здесь
        работает так же верно, как сравнение дат: ISO 8601 специально
        устроен так, чтобы лексикографический порядок совпадал
        с хронологическим.
        """
        async with self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM orders WHERE created_at >= ?",
            (moment.isoformat(),),
        ) as cursor:
            row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def count_by_status(self) -> dict[str, int]:
        """Сводка «сколько заявок в каком состоянии» — для мониторинга."""
        async with self._conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["status"]: row["cnt"] for row in rows}
