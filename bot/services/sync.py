"""Фоновая выгрузка заявок в Google Sheets.

Работает бесконечным циклом параллельно с приёмом сообщений:
    спим → берём заявки со статусом new → выгружаем → помечаем synced

Почему выгрузка не делается прямо в момент подтверждения:
  • клиент не должен ждать, пока ответит Google (0.3–2 сек, иногда больше);
  • недоступность Google не должна портить клиенту сценарий записи;
  • повтор при сбое никого не задевает — просто попробуем через минуту.

Заявка к этому моменту уже лежит в SQLite, поэтому потерять её нельзя.
"""

import asyncio
import logging

from bot.services.sheets import SheetsClient, SheetsError
from bot.storage.orders import OrderRepository

logger = logging.getLogger(__name__)

# Как часто проверять очередь. Компромисс: чаще — заявки появляются
# в таблице быстрее; реже — меньше расход квоты Google.
POLL_INTERVAL_SECONDS = 60

# Пауза после сбоя растёт: 60 → 120 → 240 … но не больше получаса.
# Так мы не долбим лежащий сервис и не жжём квоту, но и не засыпаем
# навсегда, если он поднимется.
BACKOFF_FACTOR = 2
MAX_BACKOFF_SECONDS = 30 * 60

# Сколько заявок выгружаем за один заход.
BATCH_SIZE = 50


async def sync_worker(orders: OrderRepository, sheets: SheetsClient) -> None:
    """Бесконечный цикл выгрузки. Запускается как фоновая задача."""
    delay = POLL_INTERVAL_SECONDS

    logger.info("Синхронизация с Google Sheets запущена (раз в %d сек)", POLL_INTERVAL_SECONDS)

    while True:
        try:
            await asyncio.sleep(delay)
            pending = await orders.list_pending(limit=BATCH_SIZE)

            if not pending:
                # Очередь пуста — сбрасываем возможный backoff.
                delay = POLL_INTERVAL_SECONDS
                continue

            logger.info("Выгружаю заявок: %d", len(pending))
            await sheets.append_orders(pending)

            # Отмечаем успех только ПОСЛЕ того, как Google подтвердил запись.
            for order in pending:
                await orders.mark_synced(order.id)

            logger.info("Выгружено успешно: %s", [o.id for o in pending])
            delay = POLL_INTERVAL_SECONDS

        except asyncio.CancelledError:
            # Бота останавливают — выходим из цикла молча и без паники.
            logger.info("Синхронизация остановлена")
            raise

        except SheetsError as e:
            # Ожидаемый сбой: нет сети, протух токен, кончилась квота.
            # Счётчик попыток растёт у каждой заявки; после лимита
            # (MAX_SYNC_ATTEMPTS) она получит статус failed и выпадет
            # из очереди, чтобы не блокировать остальные.
            logger.warning("Выгрузка не удалась: %s", e)
            for order in pending:
                await orders.mark_sync_failed(order.id, str(e))

            delay = min(delay * BACKOFF_FACTOR, MAX_BACKOFF_SECONDS)
            logger.info("Следующая попытка через %d сек", delay)

        except Exception:
            # Неожиданная ошибка: логируем с трейсбеком, но цикл НЕ
            # роняем — иначе бот тихо перестанет выгружать заявки,
            # продолжая принимать их. Такое молчаливое падение хуже сбоя.
            logger.exception("Непредвиденная ошибка синхронизации")
            delay = min(delay * BACKOFF_FACTOR, MAX_BACKOFF_SECONDS)
