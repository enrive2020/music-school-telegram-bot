"""Тесты диалога записи: переходы FSM, навигация, устойчивость.

Здесь работает настоящий Dispatcher с настоящими роутерами и фильтрами —
подменён только транспорт к Telegram (см. fake_telegram.py). Поэтому
тесты ловят реальные ошибки маршрутизации: например, обработчик,
перехватывающий команду, которая должна была уйти другому.
"""

from datetime import date

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from bot.catalog import Catalog
from bot.handlers.admin import router as admin_router
from bot.handlers.catalog import router as catalog_router
from bot.handlers.errors import router as errors_router
from bot.handlers.order import router as order_router
from bot.handlers.start import router as start_router
from bot.services.notify import AdminNotifier
from bot.services.schedule import ScheduleService
from bot.states.order import OrderForm
from bot.storage.database import connect, init_schema
from bot.storage.orders import OrderRepository
from tests.conftest import MOSCOW
from tests.fake_telegram import (
    CHAT_ID,
    USER_ID,
    FakeSession,
    make_callback,
    make_message,
)


@pytest.fixture
async def bot_env(catalog: Catalog, tmp_path):
    """Собранный бот с фейковым транспортом и временной базой.

    Возвращает всё, что нужно тесту: диспетчер, бот, сессию для
    проверки отправленного и хранилище состояний.
    """
    session = FakeSession()
    bot = Bot(
        token="42:TEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    conn = await connect(tmp_path / "test.db")
    await init_schema(conn)

    storage = MemoryStorage()
    dp = Dispatcher(
        storage=storage,
        catalog=catalog,
        orders=OrderRepository(conn),
        notifier=AdminNotifier(bot=bot, chat_id=None, tz=MOSCOW),
        schedule=ScheduleService(catalog.school.schedule, MOSCOW),
    )
    # Роутеры — глобальные объекты своих модулей, а aiogram разрешает
    # подключить роутер только к ОДНОМУ диспетчеру. Каждому тесту нужен
    # свежий диспетчер, поэтому сначала отвязываем роутеры от прошлого.
    # Обращение к приватному полю — сознательный компромисс: публичного
    # способа «отцепить» роутер в aiogram нет.
    routers = (
        start_router,
        admin_router,
        catalog_router,
        order_router,
        errors_router,
    )
    for router in routers:
        router._parent_router = None

    # Порядок подключения тот же, что в bot/main.py — иначе тест
    # проверял бы не ту конфигурацию, которая работает в проде.
    for router in routers:
        dp.include_router(router)

    yield SimpleEnv(dp=dp, bot=bot, session=session, storage=storage)
    await conn.close()


class SimpleEnv:
    """Контейнер окружения + пара удобных методов."""

    def __init__(self, dp: Dispatcher, bot: Bot, session: FakeSession, storage) -> None:
        self.dp = dp
        self.bot = bot
        self.session = session
        self.storage = storage
        self._key = StorageKey(bot_id=bot.id, chat_id=CHAT_ID, user_id=USER_ID)

    async def feed(self, update) -> None:
        """Прогоняет апдейт через диспетчер, как это делает polling."""
        await self.dp.feed_update(self.bot, update)

    async def state(self) -> str | None:
        return await self.storage.get_state(self._key)

    async def data(self) -> dict:
        return await self.storage.get_data(self._key)

    async def set_state(self, state) -> None:
        await self.storage.set_state(self._key, state)

    async def update_data(self, **kwargs) -> None:
        data = await self.storage.get_data(self._key)
        data.update(kwargs)
        await self.storage.set_data(self._key, data)


class TestEntry:
    async def test_start_shows_directions(self, bot_env: SimpleEnv) -> None:
        await bot_env.feed(make_message("/start"))
        assert "🎸 Гитара" in bot_env.session.buttons()

    async def test_start_resets_unfinished_form(self, bot_env: SimpleEnv) -> None:
        """/start посреди анкеты не должен оставлять клиента в состоянии."""
        await bot_env.set_state(OrderForm.name)
        await bot_env.feed(make_message("/start"))
        assert await bot_env.state() is None

    async def test_begin_order_asks_for_whom(self, bot_env: SimpleEnv) -> None:
        await bot_env.feed(make_callback("start_order:guitar"))
        assert await bot_env.state() == OrderForm.for_whom.state
        assert "Ребёнок" in bot_env.session.buttons()


class TestNameStep:
    async def test_valid_name_moves_to_phone(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.name)
        await bot_env.feed(make_message("Анна"))

        assert await bot_env.state() == OrderForm.phone.state
        assert (await bot_env.data())["name"] == "Анна"

    async def test_invalid_name_keeps_state(self, bot_env: SimpleEnv) -> None:
        """Ошибка ввода не выкидывает клиента из анкеты."""
        await bot_env.set_state(OrderForm.name)
        await bot_env.feed(make_message("Анна2"))

        assert await bot_env.state() == OrderForm.name.state
        assert "букв" in bot_env.session.last_text()

    async def test_cancel_command_works_on_name_step(self, bot_env: SimpleEnv) -> None:
        """Регрессия: /cancel не работал ни на одном текстовом шаге.

        Команда была зарегистрирована ПОСЛЕ обработчика имени, и текст
        «/cancel» уходил в валидатор — клиент получал «имя может
        состоять только из букв» вместо отмены.
        """
        await bot_env.set_state(OrderForm.name)
        await bot_env.feed(make_message("/cancel"))

        assert await bot_env.state() is None
        assert "отменена" in bot_env.session.last_text().lower()


class TestDayAndSlotSteps:
    async def test_phone_leads_to_day_choice(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.phone)
        await bot_env.feed(make_message("+79001234567"))

        assert await bot_env.state() == OrderForm.day.state
        # Кнопки дат несут ISO-дату — по ней потом строится сетка времени.
        assert any(cb.startswith("day:") for cb in bot_env.session.callback_data())

    async def test_choosing_day_shows_time_grid(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.day)
        day = _next_workday()
        await bot_env.feed(make_callback(f"day:{day.isoformat()}"))

        assert await bot_env.state() == OrderForm.time.state
        assert (await bot_env.data())["day"] == day.isoformat()
        # Время кодируется как HHMM: двоеточие сломало бы разбор.
        assert any(cb.startswith("slot:") for cb in bot_env.session.callback_data())

    async def test_choosing_slot_saves_readable_time(self, bot_env: SimpleEnv) -> None:
        day = _next_workday()
        await bot_env.set_state(OrderForm.time)
        await bot_env.update_data(day=day.isoformat())
        await bot_env.feed(make_callback(f"slot:{day.isoformat()}:1200"))

        assert await bot_env.state() == OrderForm.comment.state
        # В data лежит и машинное значение, и человекочитаемое.
        data = await bot_env.data()
        assert data["slot_hm"] == "1200"
        assert "12:00" in data["time"]

    async def test_forged_slot_is_rejected(self, bot_env: SimpleEnv) -> None:
        """callback_data — внешний ввод, слот сверяется с расписанием."""
        day = _next_workday()
        await bot_env.set_state(OrderForm.time)
        await bot_env.update_data(day=day.isoformat())
        # 03:00 — вне рабочих часов школы.
        await bot_env.feed(make_callback(f"slot:{day.isoformat()}:0300"))

        assert await bot_env.state() == OrderForm.day.state
        assert bot_env.session.alerts(), "клиент должен увидеть объяснение"

    async def test_text_on_day_step_gets_hint(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.day)
        await bot_env.feed(make_message("завтра вечером"))

        assert await bot_env.state() == OrderForm.day.state
        assert "кнопк" in bot_env.session.last_text().lower()


class TestNavigation:
    async def test_back_from_time_returns_to_days(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.time)
        await bot_env.update_data(day=_next_workday().isoformat())
        await bot_env.feed(make_callback("order_back"))

        assert await bot_env.state() == OrderForm.day.state

    async def test_back_from_comment_rebuilds_slots(self, bot_env: SimpleEnv) -> None:
        """Возврат к времени должен пересобрать сетку выбранного дня."""
        day = _next_workday()
        await bot_env.set_state(OrderForm.comment)
        await bot_env.update_data(day=day.isoformat(), slot_hm="1200")
        await bot_env.feed(make_callback("order_back"))

        assert await bot_env.state() == OrderForm.time.state
        assert any(
            cb.startswith(f"slot:{day.isoformat()}")
            for cb in bot_env.session.callback_data()
        )

    async def test_back_from_name_returns_to_for_whom(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.name)
        await bot_env.feed(make_callback("order_back"))
        assert await bot_env.state() == OrderForm.for_whom.state

    async def test_cancel_button_clears_state(self, bot_env: SimpleEnv) -> None:
        await bot_env.set_state(OrderForm.comment)
        await bot_env.feed(make_callback("order_cancel"))
        assert await bot_env.state() is None


class TestStaleButtons:
    async def test_button_after_finish_gets_explanation(
        self, bot_env: SimpleEnv
    ) -> None:
        """Клиент пролистал вверх и нажал кнопку завершённой формы."""
        await bot_env.feed(make_callback("order_back"))  # состояния нет

        assert bot_env.session.alerts(), "должен быть всплывающий ответ"
        assert "/start" in bot_env.session.alerts()[-1]

    async def test_stale_day_button(self, bot_env: SimpleEnv) -> None:
        await bot_env.feed(make_callback("day:2026-08-10"))
        assert bot_env.session.alerts()


class TestOrderSaving:
    async def test_confirm_saves_order(self, bot_env: SimpleEnv, catalog) -> None:
        await bot_env.set_state(OrderForm.confirm)
        await bot_env.update_data(
            direction_id="guitar",
            for_whom="child",
            name="Анна",
            phone="+79001234567",
            time="10 августа (пн), 12:00",
            comment="",
        )
        await bot_env.feed(make_callback("order_confirm"))

        assert await bot_env.state() is None
        # Номер заявки из базы — доказательство, что запись состоялась.
        assert "№1" in bot_env.session.last_text()

    async def test_saved_order_keeps_snapshot(self, bot_env: SimpleEnv) -> None:
        """Заявка хранит название и цену на момент оформления."""
        repo = bot_env.dp.workflow_data["orders"]
        await bot_env.set_state(OrderForm.confirm)
        await bot_env.update_data(
            direction_id="guitar",
            for_whom="adult",
            name="Пётр",
            phone="+79001234567",
            time="10 августа (пн), 12:00",
        )
        await bot_env.feed(make_callback("order_confirm"))

        order = await repo.get(1)
        assert order.direction_title == "Гитара"
        assert order.price_per_lesson == 1200
        assert order.name == "Пётр"


def _next_workday() -> date:
    """Ближайший будний день после завтра — заведомо в горизонте
    и заведомо не «сегодня», где часть слотов уже прошла."""
    day = date.today()
    for _ in range(10):
        day = date.fromordinal(day.toordinal() + 1)
        if day.weekday() < 5:  # пн-пт
            return day
    raise AssertionError("не нашёлся будний день")
