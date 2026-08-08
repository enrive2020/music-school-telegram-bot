"""Тесты административных команд.

Главное здесь — не форматирование ответов, а КОНТРОЛЬ ДОСТУПА:
/orders показывает имена и телефоны клиентов. Ошибка в фильтре
означает утечку персональных данных, поэтому проверяется отдельно
и с обеих сторон: и что админ видит, и что посторонний не видит.
"""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.catalog import Catalog
from bot.handlers.admin import router as admin_router
from bot.services.notify import AdminNotifier
from bot.storage.database import connect, init_schema
from bot.storage.orders import OrderRepository
from tests.conftest import MOSCOW
from tests.fake_telegram import CHAT_ID, FakeSession, make_message
from tests.test_storage import make_order

# Чат администраторов отличается от чата клиента из fake_telegram.
ADMIN_CHAT_ID = -100_123_456


async def build_env(catalog: Catalog, tmp_path, admin_chat_id: int | None):
    """Собирает бота с заданным админ-чатом."""
    session = FakeSession()
    bot = Bot(
        token="42:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    conn = await connect(tmp_path / "admin.db")
    await init_schema(conn)
    repo = OrderRepository(conn)

    admin_router._parent_router = None
    dp = Dispatcher(
        storage=MemoryStorage(),
        catalog=catalog,
        orders=repo,
        notifier=AdminNotifier(bot=bot, chat_id=None, tz=MOSCOW),
        admin_chat_id=admin_chat_id,
    )
    dp.include_router(admin_router)
    return dp, bot, session, repo, conn


@pytest.fixture
async def admin_env(catalog: Catalog, tmp_path):
    """Бот, где админский чат настроен."""
    dp, bot, session, repo, conn = await build_env(catalog, tmp_path, ADMIN_CHAT_ID)
    yield dp, bot, session, repo
    await conn.close()


def admin_message(text: str):
    """Сообщение из чата администраторов."""
    return make_message(text, chat_id=ADMIN_CHAT_ID)


class TestAccessControl:
    async def test_stranger_gets_no_answer(self, admin_env) -> None:
        """Команда из чужого чата не должна отвечать ВООБЩЕ.

        Не «доступ запрещён», а молчание: сообщение об отказе
        подтвердило бы, что команда существует.
        """
        dp, bot, session, _ = admin_env
        await dp.feed_update(bot, make_message("/orders"))  # обычный чат
        assert session.texts() == []

    async def test_admin_gets_answer(self, admin_env) -> None:
        dp, bot, session, _ = admin_env
        await dp.feed_update(bot, admin_message("/stats"))
        assert session.texts()

    async def test_closed_when_admin_chat_not_configured(
        self, catalog: Catalog, tmp_path
    ) -> None:
        """Не настроено = закрыто для всех, а не открыто всем.

        Регрессия против опасного дефолта: если бы фильтр при
        admin_chat_id=None пропускал всех, любой клиент увидел бы
        телефоны других клиентов.
        """
        dp, bot, session, _, conn = await build_env(catalog, tmp_path, None)
        try:
            await dp.feed_update(bot, admin_message("/orders"))
            assert session.texts() == []
        finally:
            await conn.close()

    async def test_chatid_is_open_to_everyone(self, admin_env) -> None:
        """/chatid нужна, чтобы НАСТРОИТЬ админский чат, — иначе
        замкнутый круг. Секретов она не раскрывает."""
        dp, bot, session, _ = admin_env
        await dp.feed_update(bot, make_message("/chatid"))
        assert session.texts()


class TestStats:
    async def test_empty_base(self, admin_env) -> None:
        dp, bot, session, _ = admin_env
        await dp.feed_update(bot, admin_message("/stats"))
        assert "нет" in session.last_text().lower()

    async def test_counts_orders(self, admin_env) -> None:
        dp, bot, session, repo = admin_env
        await repo.create(make_order())
        await repo.create(make_order())

        await dp.feed_update(bot, admin_message("/stats"))
        assert "2" in session.last_text()

    async def test_warns_about_failed_orders(self, admin_env) -> None:
        """Заявка, не попавшая в таблицу, должна быть заметна."""
        dp, bot, session, repo = admin_env
        order = await repo.create(make_order())
        for _ in range(5):  # исчерпываем лимит попыток
            await repo.mark_sync_failed(order.id, "нет сети")

        await dp.feed_update(bot, admin_message("/stats"))
        assert "⚠️" in session.last_text()


class TestOrders:
    async def test_shows_contacts(self, admin_env) -> None:
        dp, bot, session, repo = admin_env
        await repo.create(make_order(name="Анна П.", phone="+79001234567"))

        await dp.feed_update(bot, admin_message("/orders"))
        text = session.last_text()
        assert "Анна П." in text
        assert "+7 900 123-45-67" in text

    async def test_newest_first(self, admin_env) -> None:
        dp, bot, session, repo = admin_env
        await repo.create(make_order(name="Первый"))
        await repo.create(make_order(name="Последний"))

        await dp.feed_update(bot, admin_message("/orders"))
        text = session.last_text()
        assert text.index("Последний") < text.index("Первый")

    async def test_escapes_hostile_name(self, admin_env) -> None:
        """Имя клиента — пользовательский ввод.

        Без экранирования «<b>Аня» сломает HTML-разметку, и Telegram
        отвергнет сообщение целиком — админ не получит ничего.
        """
        dp, bot, session, repo = admin_env
        await repo.create(make_order(name="<b>Аня</b>"))

        await dp.feed_update(bot, admin_message("/orders"))
        assert "&lt;b&gt;" in session.last_text()

    async def test_empty_base(self, admin_env) -> None:
        dp, bot, session, _ = admin_env
        await dp.feed_update(bot, admin_message("/orders"))
        assert "нет" in session.last_text().lower()
