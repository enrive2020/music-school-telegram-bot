"""Подключение к SQLite и создание схемы.

Схема создаётся при каждом старте через CREATE TABLE IF NOT EXISTS —
для проекта такого размера это проще и надёжнее, чем система миграций.
Когда таблиц станет много, сюда придёт alembic или yoyo-migrations.
"""

import logging
import sqlite3
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Схема заявок.
#
# Почему created_at — TEXT: у SQLite нет типа «дата». Общепринятый
# способ — хранить строку ISO 8601 в UTC: она сортируется как обычный
# текст, читается человеком и однозначно разбирается обратно.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL,

    telegram_user_id  INTEGER NOT NULL,
    telegram_username TEXT,

    direction_id      TEXT    NOT NULL,
    direction_title   TEXT    NOT NULL,
    price_per_lesson  INTEGER NOT NULL,

    for_whom          TEXT    NOT NULL,
    name              TEXT    NOT NULL,
    phone             TEXT    NOT NULL,
    preferred_time    TEXT    NOT NULL,
    comment           TEXT    NOT NULL DEFAULT '',

    status            TEXT    NOT NULL DEFAULT 'new',
    sync_attempts     INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    synced_at         TEXT
);

-- Индекс под главный запрос фоновой выгрузки:
-- «дай заявки со статусом new, самые старые первыми».
CREATE INDEX IF NOT EXISTS idx_orders_status_created
    ON orders (status, created_at);
"""


async def connect(db_path: str | Path) -> aiosqlite.Connection:
    """Открывает соединение с базой и готовит её к работе."""
    path = Path(db_path)
    # Папку data/ может не существовать при первом запуске или после
    # клонирования репозитория — создаём молча.
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(path)

    # row_factory=Row: строки приходят как словареподобные объекты,
    # и мы обращаемся row["name"] вместо row[7]. Меньше шансов
    # сломать всё, добавив колонку в середину таблицы.
    conn.row_factory = aiosqlite.Row

    # WAL (write-ahead logging): чтение не блокирует запись и наоборот.
    # Для бота, который параллельно принимает заявки и выгружает их
    # фоновой задачей, это заметно снижает шанс «database is locked».
    #
    # Но WAL требует разделяемой памяти (файл -shm), а она есть не везде:
    # не работает на сетевых файловых системах (NFS, SMB) и на папках,
    # проброшенных в контейнер с Windows-хоста. Там включение WAL
    # роняет запуск с «unable to open database file».
    #
    # Поэтому режим необязателен: не вышло — работаем на журнале по
    # умолчанию. Это медленнее при одновременных чтении и записи,
    # но полностью корректно. Ронять бота из-за оптимизации нельзя.
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as e:
        logger.warning(
            "WAL недоступен на этой файловой системе (%s). "
            "Работаем в обычном режиме журнала — это безопасно, "
            "но при высокой нагрузке возможны задержки записи.",
            e,
        )

    # Проверять внешние ключи (пригодится, когда появятся связанные таблицы).
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()

    logger.info("База подключена: %s", path.resolve())
    return conn


async def init_schema(conn: aiosqlite.Connection) -> None:
    """Создаёт таблицы и индексы, если их ещё нет."""
    await conn.executescript(_SCHEMA)
    await conn.commit()
    logger.info("Схема базы готова")
