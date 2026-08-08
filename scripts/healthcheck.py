"""Самодиагностика: всё ли на месте для запуска бота.

Запуск локально:
    .venv\\Scripts\\python.exe -m scripts.healthcheck

Внутри контейнера:
    docker compose run --rm -T bot python -m scripts.healthcheck

Зачем нужна: после развёртывания на сервере проверять настройки
по одной, вглядываясь в логи, мучительно. Скрипт проходит по всем
внешним зависимостям и говорит, что именно не готово.

Google Sheets намеренно НЕ проверяется — для этого есть отдельный
scripts/check_sheets.py, который делает реальный запрос к API.
"""

import asyncio
import os
import sys

from bot.catalog import CatalogError, load_catalog
from bot.settings import load_settings
from bot.storage.database import connect, init_schema
from bot.storage.orders import OrderRepository

OK = "  ✓"
FAIL = "  ✗"


async def main() -> int:
    problems = 0

    print("── Окружение ──")
    print(f"{OK} Python {sys.version.split()[0]}")
    # getuid есть только на Unix — на Windows его просто не существует.
    if hasattr(os, "getuid"):
        uid = os.getuid()
        marker = OK if uid != 0 else FAIL
        print(f"{marker} пользователь: uid={uid}" + (" (root!)" if uid == 0 else ""))
        if uid == 0:
            problems += 1

    print("\n── Настройки ──")
    try:
        settings = load_settings()
    except Exception as e:
        print(f"{FAIL} .env не прочитан: {e}")
        return 1
    print(f"{OK} BOT_TOKEN задан")

    if settings.admin_chat_id is None:
        print(f"{FAIL} ADMIN_CHAT_ID пуст — уведомления и админ-команды выключены")
        problems += 1
    else:
        print(f"{OK} ADMIN_CHAT_ID: {settings.admin_chat_id}")

    if settings.google_sheet_id:
        print(f"{OK} GOOGLE_SHEET_ID задан")
        if settings.google_credentials_file.exists():
            print(f"{OK} ключ Google на месте")
        else:
            print(f"{FAIL} нет файла {settings.google_credentials_file}")
            problems += 1
    else:
        print(f"{FAIL} GOOGLE_SHEET_ID пуст — выгрузка в таблицу выключена")
        problems += 1

    print("\n── Конфиг школы ──")
    try:
        catalog = load_catalog(settings.catalog_path)
    except CatalogError as e:
        print(f"{FAIL} {e}")
        return 1
    print(f"{OK} {catalog.school.name}, направлений: {len(catalog.directions)}")
    print(f"{OK} часовой пояс: {catalog.school.tzinfo}")

    schedule = catalog.school.schedule
    workdays = len(schedule.week)
    print(
        f"{OK} расписание: {workdays} рабочих дней, шаг {schedule.slot_minutes} мин, "
        f"горизонт {schedule.booking_horizon_days} дн"
    )

    print("\n── База заявок ──")
    try:
        conn = await connect(settings.database_path)
        await init_schema(conn)
        stats = await OrderRepository(conn).count_by_status()
        await conn.close()
    except Exception as e:
        print(f"{FAIL} база недоступна: {e}")
        return 1
    print(f"{OK} {settings.database_path} доступна на запись")
    print(f"{OK} заявок в базе: {sum(stats.values())} {stats or ''}")

    print()
    if problems:
        print(f"Готов к запуску, но есть замечания: {problems}")
    else:
        print("Всё готово.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
