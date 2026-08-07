"""Выгрузка заявок в Google Sheets.

Важная особенность: gspread — БЛОКИРУЮЩАЯ библиотека (обычные HTTP-запросы,
без async). Если вызвать её напрямую из хендлера, встанет весь бот —
пока Google думает секунду, никто другой не получит ответа.

Решение — asyncio.to_thread(): функция выполняется в отдельном потоке,
а event loop в это время свободен. Ровно та же идея, что внутри
aiosqlite, только здесь мы делаем это руками.

Альтернатива — искать асинхронный клиент Google (gspread-asyncio);
но это лишняя зависимость ради двух вызовов, а to_thread — штатный
инструмент стандартной библиотеки.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread

from bot.storage.models import Order

logger = logging.getLogger(__name__)

# Заголовки таблицы. Порядок обязан совпадать с _order_to_row().
HEADERS = [
    "№",
    "Дата заявки",
    "Направление",
    "Цена",
    "Для кого",
    "Имя",
    "Телефон",
    "Удобное время",
    "Комментарий",
    "Telegram",
]

FOR_WHOM_LABELS = {"child": "Ребёнок", "adult": "Взрослый"}


class SheetsError(Exception):
    """Не удалось поработать с таблицей. Несёт понятный текст для лога."""


class SheetsClient:
    """Тонкая обёртка над gspread под нашу задачу.

    Хранит подключение лениво: клиент создаётся при первом обращении.
    Так бот стартует, даже если Google в этот момент недоступен —
    приём заявок от этого не зависит.
    """

    def __init__(self, credentials_file: Path, sheet_id: str, tz: ZoneInfo) -> None:
        self._credentials_file = credentials_file
        self._sheet_id = sheet_id
        self._tz = tz
        self._worksheet: gspread.Worksheet | None = None

    # ── Синхронная часть: выполняется в отдельном потоке ──
    def _connect_sync(self) -> gspread.Worksheet:
        """Подключается и возвращает лист, попутно создав заголовки."""
        client = gspread.service_account(filename=str(self._credentials_file))
        spreadsheet = client.open_by_key(self._sheet_id)
        worksheet = spreadsheet.sheet1

        # Если лист пуст — пишем шапку. Проверяем именно первую строку,
        # а не весь лист: так повторный запуск ничего не портит.
        first_row = worksheet.row_values(1)
        if not first_row:
            worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
            # Закрепляем шапку, чтобы она не уезжала при прокрутке —
            # мелочь, но владелец таблицы это заметит.
            worksheet.freeze(rows=1)
            logger.info("Заголовки таблицы созданы")

        return worksheet

    def _order_to_row(self, order: Order) -> list:
        """Превращает заявку в строку таблицы."""
        # В базе время в UTC — показываем владельцу в его поясе.
        local_time = order.created_at.astimezone(self._tz)
        username = f"@{order.telegram_username}" if order.telegram_username else ""
        return [
            order.id,
            local_time.strftime("%d.%m.%Y %H:%M"),
            order.direction_title,
            order.price_per_lesson,
            FOR_WHOM_LABELS.get(order.for_whom, order.for_whom),
            order.name,
            # Апостроф в начале — чтобы Google не съел «+» и не
            # превратил номер в формулу или число.
            f"'{order.phone}",
            order.preferred_time,
            order.comment,
            username,
        ]

    def _append_sync(self, orders: list[Order]) -> None:
        if self._worksheet is None:
            self._worksheet = self._connect_sync()
        rows = [self._order_to_row(order) for order in orders]
        # append_rows отправляет ВСЕ строки одним запросом.
        # У Google Sheets квота ~60 запросов в минуту, поэтому пачкой
        # выгружать и быстрее, и безопаснее, чем по одной.
        self._worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    # ── Асинхронная обёртка ──
    async def append_orders(self, orders: list[Order]) -> None:
        """Дописывает заявки в конец таблицы.

        Любая ошибка библиотеки превращается в SheetsError — вызывающий
        код не должен разбираться в тонкостях gspread.
        """
        if not orders:
            return
        try:
            await asyncio.to_thread(self._append_sync, orders)
        except gspread.exceptions.APIError as e:
            # Сбрасываем соединение: возможно, протух токен или
            # изменились права — на следующей попытке переподключимся.
            self._worksheet = None
            raise SheetsError(f"Google API вернул ошибку: {e}") from e
        except Exception as e:
            self._worksheet = None
            raise SheetsError(f"Не удалось записать в таблицу: {e}") from e
