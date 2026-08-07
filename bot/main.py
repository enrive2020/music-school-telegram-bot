"""Точка входа. Запуск:  .venv\\Scripts\\python.exe -m bot.main

Схема работы:
    Bot        — соединение с Telegram API (умеет отправлять сообщения).
    Dispatcher — принимает входящие события и раздаёт их по роутерам.
    Router'ы   — куски диалога, живут в bot/handlers/.

Получение сообщений — long polling: бот сам спрашивает у Telegram
«есть что-нибудь для меня?» и ждёт. Работает откуда угодно, даже
из-за домашнего роутера. Альтернатива — webhook (Telegram сам стучится
на твой сервер): быстрее, но требует публичный HTTPS-адрес.
Для разработки и малого бизнеса polling — стандартный выбор.
"""

import asyncio
import logging
import sys
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.catalog import CatalogError, load_catalog
from bot.handlers.admin import router as admin_router
from bot.handlers.catalog import router as catalog_router
from bot.handlers.errors import router as errors_router
from bot.handlers.order import router as order_router
from bot.handlers.start import router as start_router
from bot.logging_setup import setup_logging
from bot.services.notify import AdminNotifier
from bot.services.schedule import ScheduleService
from bot.services.sheets import SheetsClient
from bot.services.sync import sync_worker
from bot.settings import load_settings
from bot.storage.database import connect, init_schema
from bot.storage.orders import OrderRepository


async def main() -> None:
    # Настройки читаются один раз на старте. Если токена нет —
    # упадём прямо здесь, с понятным текстом, а не при первом сообщении.
    settings = load_settings()

    # Логирование настраиваем сразу после настроек: всё, что случится
    # дальше, должно попасть и в консоль, и в файл с ротацией.
    setup_logging(settings.log_level, settings.log_file)

    # Каталог — тоже на старте и тоже «падаем сразу»: опечатка
    # в YAML не должна доживать до первого клиента.
    try:
        catalog = load_catalog(settings.catalog_path)
    except CatalogError as e:
        logging.error("Каталог не загружен.\n%s", e)
        sys.exit(1)
    logging.info(
        "Каталог загружен: %s, направлений: %d",
        catalog.school.name,
        len(catalog.directions),
    )

    # База — единственный ресурс, который нужно явно закрывать,
    # поэтому дальше всё оборачиваем в try/finally.
    conn = await connect(settings.database_path)
    await init_schema(conn)
    orders = OrderRepository(conn)

    bot = Bot(
        token=settings.bot_token,
        # parse_mode=HTML: в текстах сообщений можно использовать
        # <b>жирный</b> и <i>курсив</i>, не указывая это в каждом answer().
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # storage — где хранятся состояния FSM и введённые данные.
    # MemoryStorage держит их в оперативке: просто и быстро, но при
    # перезапуске бота незаконченные анкеты пропадают. Для продакшена
    # заменяется на RedisStorage без изменения обработчиков.
    #
    # Уведомления администраторам. Без ADMIN_CHAT_ID бот работает
    # полноценно — просто молча, что удобно при разработке.
    notifier = AdminNotifier(
        bot=bot, chat_id=settings.admin_chat_id, tz=catalog.school.tzinfo
    )
    if not notifier.enabled:
        logging.warning(
            "ADMIN_CHAT_ID не задан — уведомления о заявках выключены. "
            "Узнать ID чата можно командой /chatid"
        )

    # Всё, что передано именованными аргументами, aiogram подставляет
    # в хендлеры по имени параметра (dependency injection). Так репозиторий
    # попадает в confirm_order, не будучи глобальной переменной.
    # Расписание: превращает недельный шаблон из конфига в конкретные
    # даты и слоты. Единственная точка, где рождается список доступного
    # времени, — поэтому будущая проверка занятости встанет сюда же.
    schedule = ScheduleService(catalog.school.schedule, catalog.school.tzinfo)
    logging.info(
        "Расписание: шаг %d мин, горизонт %d дн, доступных дней сейчас: %d",
        catalog.school.schedule.slot_minutes,
        catalog.school.schedule.booking_horizon_days,
        len(schedule.available_days()),
    )

    dp = Dispatcher(
        storage=MemoryStorage(),
        catalog=catalog,
        orders=orders,
        notifier=notifier,
        schedule=schedule,
    )

    # Порядок важен: роутеры команд (start, admin) идут ПЕРВЫМИ, чтобы
    # /start и /chatid срабатывали даже посреди анкеты, а не
    # воспринимались как текстовый ввод шага.
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(catalog_router)
    dp.include_router(order_router)
    # Обработчик ошибок — последним: он ловит то, что упало в остальных.
    dp.include_router(errors_router)

    # Фоновая выгрузка в Google Sheets. Включается только если задан
    # GOOGLE_SHEET_ID: без него бот полноценно работает на одном SQLite,
    # что удобно для разработки и как аварийный режим.
    sync_task: asyncio.Task | None = None
    if settings.google_sheet_id:
        sheets = SheetsClient(
            credentials_file=settings.google_credentials_file,
            sheet_id=settings.google_sheet_id,
            tz=catalog.school.tzinfo,
        )
        # create_task запускает корутину «рядом» с polling: обе живут
        # в одном event loop и по очереди отдают друг другу управление.
        sync_task = asyncio.create_task(sync_worker(orders, sheets, notifier))
    else:
        logging.warning(
            "GOOGLE_SHEET_ID не задан — выгрузка в таблицу выключена, "
            "заявки копятся в локальной базе"
        )

    try:
        logging.info("Бот запускается (long polling)…")
        # start_polling крутится бесконечно и сам аккуратно закрывает
        # соединения при остановке (Ctrl+C).
        await dp.start_polling(bot)
    finally:
        # Останавливаем фоновую задачу и ждём, пока она свернётся.
        if sync_task is not None:
            sync_task.cancel()
            # suppress: cancel() всегда поднимает CancelledError —
            # это штатное завершение, а не ошибка.
            with suppress(asyncio.CancelledError):
                await sync_task

        # Закрываем базу в любом случае — даже если бот падает
        # с исключением. Иначе последняя запись может не долететь на диск.
        await conn.close()
        logging.info("База закрыта")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")
