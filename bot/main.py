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

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.catalog import CatalogError, load_catalog
from bot.handlers.catalog import router as catalog_router
from bot.handlers.start import router as start_router
from bot.settings import load_settings


async def main() -> None:
    # Пока логируем в консоль. Файл и полноценный формат — в Фазе 7.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # Настройки читаются один раз на старте. Если токена нет —
    # упадём прямо здесь, с понятным текстом, а не при первом сообщении.
    settings = load_settings()

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

    bot = Bot(
        token=settings.bot_token,
        # parse_mode=HTML: в текстах сообщений можно использовать
        # <b>жирный</b> и <i>курсив</i>, не указывая это в каждом answer().
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Всё, что передано сюда именованными аргументами, aiogram будет
    # подставлять в хендлеры по имени параметра (dependency injection).
    dp = Dispatcher(catalog=catalog)

    # Подключаем куски диалога. С каждой фазой роутеров будет больше.
    dp.include_router(start_router)
    dp.include_router(catalog_router)

    logging.info("Бот запускается (long polling)…")
    # start_polling крутится бесконечно и сам аккуратно закрывает
    # соединения при остановке (Ctrl+C).
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")
