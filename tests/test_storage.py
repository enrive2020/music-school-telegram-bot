"""Тесты репозитория заявок.

Асинхронные: база у нас на aiosqlite. Благодаря asyncio_mode=auto
в pytest.ini достаточно написать «async def test_…» — плагин сам
запустит тест в event loop.

Каждый тест получает СВОЮ пустую базу (фикстура repo). Тесты, делящие
одну базу, начинают зависеть от порядка запуска: заявка от соседнего
теста ломает подсчёт, и падение выглядит случайным.
"""

from collections.abc import AsyncIterator

import pytest

from bot.storage.database import connect, init_schema
from bot.storage.models import NewOrder, OrderStatus
from bot.storage.orders import MAX_SYNC_ATTEMPTS, OrderRepository


@pytest.fixture
async def repo(tmp_path) -> AsyncIterator[OrderRepository]:
    """Репозиторий поверх временной базы.

    tmp_path — встроенная фикстура pytest: уникальная папка на тест,
    удаляется автоматически. Ничего чистить руками не нужно.

    yield вместо return: код после yield выполнится ПОСЛЕ теста —
    так закрывается соединение, даже если тест упал.
    """
    conn = await connect(tmp_path / "test.db")
    await init_schema(conn)
    yield OrderRepository(conn)
    await conn.close()


def make_order(**overrides) -> NewOrder:
    """Заявка с разумными значениями по умолчанию.

    Тест переопределяет только то поле, которое проверяет, — так
    видно, что именно важно в конкретном тесте.
    """
    data = dict(
        telegram_user_id=555,
        telegram_username="client",
        direction_id="guitar",
        direction_title="Гитара",
        price_per_lesson=1200,
        for_whom="child",
        name="Анна",
        phone="+79001234567",
        preferred_time="10 августа (пн), 18:00",
        comment="",
    )
    data.update(overrides)
    return NewOrder(**data)


class TestCreate:
    async def test_assigns_id_and_status(self, repo: OrderRepository) -> None:
        order = await repo.create(make_order())
        assert order.id == 1
        assert order.status == OrderStatus.NEW

    async def test_ids_increment(self, repo: OrderRepository) -> None:
        first = await repo.create(make_order())
        second = await repo.create(make_order())
        assert second.id == first.id + 1

    async def test_survives_reread(self, repo: OrderRepository) -> None:
        """Данные должны вернуться из базы такими же."""
        created = await repo.create(make_order(name="Анна П."))
        loaded = await repo.get(created.id)
        assert loaded.name == "Анна П."
        assert loaded.phone == created.phone
        assert loaded.preferred_time == created.preferred_time

    async def test_created_at_is_timezone_aware(self, repo: OrderRepository) -> None:
        """Время в UTC и с таймзоной: наивные datetime потом
        невозможно корректно перевести в пояс школы."""
        order = await repo.create(make_order())
        loaded = await repo.get(order.id)
        assert loaded.created_at.tzinfo is not None

    async def test_survives_sql_injection_attempt(self, repo: OrderRepository) -> None:
        """Имя — пользовательский ввод; параметры запроса его экранируют."""
        evil = "Роберт'); DROP TABLE orders;--"
        order = await repo.create(make_order(name=evil))
        loaded = await repo.get(order.id)
        assert loaded.name == evil
        # Таблица цела — второй заявке есть куда лечь.
        assert (await repo.create(make_order())).id == order.id + 1

    async def test_unknown_id_returns_none(self, repo: OrderRepository) -> None:
        assert await repo.get(999) is None


class TestSyncLifecycle:
    async def test_new_orders_are_pending(self, repo: OrderRepository) -> None:
        await repo.create(make_order())
        await repo.create(make_order())
        assert len(await repo.list_pending()) == 2

    async def test_synced_leaves_queue(self, repo: OrderRepository) -> None:
        order = await repo.create(make_order())
        await repo.mark_synced(order.id)

        loaded = await repo.get(order.id)
        assert loaded.status == OrderStatus.SYNCED
        assert loaded.synced_at is not None
        assert await repo.list_pending() == []

    async def test_failure_increments_attempts(self, repo: OrderRepository) -> None:
        order = await repo.create(make_order())
        await repo.mark_sync_failed(order.id, "нет сети")

        loaded = await repo.get(order.id)
        assert loaded.sync_attempts == 1
        assert loaded.last_error == "нет сети"
        # Одна неудача не выводит заявку из очереди — попробуем ещё.
        assert loaded.status == OrderStatus.NEW

    async def test_becomes_failed_after_limit(self, repo: OrderRepository) -> None:
        """Заявка не должна вечно долбиться в сломанный сервис."""
        order = await repo.create(make_order())
        for _ in range(MAX_SYNC_ATTEMPTS):
            await repo.mark_sync_failed(order.id, "ошибка")

        loaded = await repo.get(order.id)
        assert loaded.status == OrderStatus.FAILED
        # И выбывает из очереди, чтобы не блокировать остальные.
        assert await repo.list_pending() == []

    async def test_long_error_is_truncated(self, repo: OrderRepository) -> None:
        """Полный трейсбек в базе не нужен — он есть в логах."""
        order = await repo.create(make_order())
        await repo.mark_sync_failed(order.id, "x" * 5000)
        assert len((await repo.get(order.id)).last_error) <= 500

    async def test_pending_is_oldest_first(self, repo: OrderRepository) -> None:
        """Выгружаем в порядке поступления."""
        ids = [(await repo.create(make_order())).id for _ in range(3)]
        assert [o.id for o in await repo.list_pending()] == ids


class TestQueries:
    async def test_recent_is_newest_first(self, repo: OrderRepository) -> None:
        """Админу и отладке нужны свежие заявки сверху."""
        ids = [(await repo.create(make_order())).id for _ in range(3)]
        assert [o.id for o in await repo.list_recent()] == list(reversed(ids))

    async def test_count_by_status(self, repo: OrderRepository) -> None:
        synced = await repo.create(make_order())
        await repo.create(make_order())
        await repo.mark_synced(synced.id)

        assert await repo.count_by_status() == {"new": 1, "synced": 1}

    async def test_count_on_empty_base(self, repo: OrderRepository) -> None:
        assert await repo.count_by_status() == {}
