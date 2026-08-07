"""Уведомления администраторам школы.

Принцип: сбой уведомления НЕ должен ломать сценарий клиента.
Заявка к этому моменту уже сохранена в базе, поэтому если бота
выкинули из чата администраторов или ID указан неверно — мы пишем
об этом в лог, но клиент по-прежнему получает своё «Заявка принята».

Отсюда общий вид всех методов: try/except вокруг отправки, наружу
исключения не выпускаем.
"""

import logging
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from bot.storage.models import Order
from bot.validators import format_phone_display

logger = logging.getLogger(__name__)

FOR_WHOM_LABELS = {"child": "Ребёнок", "adult": "Взрослый"}


class AdminNotifier:
    """Отправка сообщений в чат администраторов."""

    def __init__(self, bot: Bot, chat_id: int | None, tz: ZoneInfo) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._tz = tz

    @property
    def enabled(self) -> bool:
        return self._chat_id is not None

    async def _send(self, text: str) -> bool:
        """Отправляет сообщение. Возвращает True при успехе."""
        if self._chat_id is None:
            return False
        try:
            await self._bot.send_message(self._chat_id, text)
            return True
        except TelegramAPIError as e:
            # Самые частые причины: бота удалили из чата, неверный
            # ADMIN_CHAT_ID, бот не был добавлен в группу.
            logger.error(
                "Не удалось отправить уведомление в чат %s: %s", self._chat_id, e
            )
            return False

    async def notify_new_order(self, order: Order) -> None:
        """Сообщает администраторам о новой заявке."""
        local_time = order.created_at.astimezone(self._tz)

        # escape() обязателен: имя и комментарий пришли от клиента,
        # а мы отправляем сообщение в режиме HTML. Без экранирования
        # имя вида «<b>Аня» сломает разметку, и Telegram отклонит
        # сообщение целиком — уведомление просто не дойдёт.
        name = escape(order.name)
        comment = escape(order.comment) if order.comment else "—"
        preferred_time = escape(order.preferred_time)

        # Ссылка на профиль: администратор может написать клиенту в один клик.
        if order.telegram_username:
            contact = f"@{escape(order.telegram_username)}"
        else:
            # У пользователя нет username — даём прямую ссылку по id.
            contact = f'<a href="tg://user?id={order.telegram_user_id}">профиль</a>'

        text = (
            f"🔔 <b>Новая заявка №{order.id}</b>\n\n"
            f"🎵 {escape(order.direction_title)} — {order.price_per_lesson} ₽\n"
            f"👤 {FOR_WHOM_LABELS.get(order.for_whom, order.for_whom)}\n"
            f"📝 Имя: {name}\n"
            # <code> — номер копируется одним нажатием.
            f"📞 <code>{format_phone_display(order.phone)}</code>\n"
            f"🕐 Удобно: {preferred_time}\n"
            f"💬 Комментарий: {comment}\n\n"
            f"Telegram: {contact}\n"
            f"Оформлена: {local_time.strftime('%d.%m.%Y в %H:%M')}"
        )

        if await self._send(text):
            logger.info("Уведомление о заявке #%s отправлено", order.id)

    async def notify_sync_failed(self, order: Order, error: str) -> None:
        """Заявка исчерпала попытки выгрузки и требует внимания человека.

        Это важный алерт: без него владелец обнаружит, что таблица
        не обновляется, спустя неделю — и не поймёт, каких заявок в ней нет.
        """
        text = (
            f"⚠️ <b>Заявка №{order.id} не попала в таблицу</b>\n\n"
            f"Все попытки выгрузки исчерпаны. Данные сохранены в базе бота "
            f"и не потеряны, но в Google Sheets их нет.\n\n"
            f"📝 {escape(order.name)}\n"
            f"📞 <code>{format_phone_display(order.phone)}</code>\n"
            f"🎵 {escape(order.direction_title)}\n\n"
            f"Причина: <code>{escape(error[:200])}</code>"
        )
        await self._send(text)
        logger.warning("Отправлен алерт о невыгруженной заявке #%s", order.id)
