"""Служебные и административные команды.

/chatid  — показывает ID текущего чата. Доступна всем: без неё
           невозможно узнать значение для ADMIN_CHAT_ID (у групп он
           отрицательный и в интерфейсе Telegram не показывается).
           Ничего секретного не раскрывает.

/stats   — сводка по заявкам.
/orders  — последние заявки с контактами.

Последние две показывают персональные данные клиентов, поэтому
закрыты фильтром IsAdminChat.
"""

import logging
from datetime import timedelta
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.catalog import Catalog
from bot.filters import IsAdminChat
from bot.storage.models import OrderStatus, utc_now
from bot.storage.orders import OrderRepository
from bot.validators import format_phone_display

router = Router(name="admin")
logger = logging.getLogger(__name__)

# Сколько заявок показывает /orders. Больше не нужно: Telegram режет
# сообщения длиннее 4096 символов, да и читать простыню неудобно.
RECENT_LIMIT = 10

STATUS_LABELS = {
    OrderStatus.NEW.value: "⏳ ждут выгрузки",
    OrderStatus.SYNCED.value: "✅ в таблице",
    OrderStatus.FAILED.value: "⚠️ не выгружены",
}

FOR_WHOM_LABELS = {"child": "ребёнок", "adult": "взрослый"}

# Понятные названия типов чата вместо технических.
CHAT_TYPES = {
    "private": "личный чат",
    "group": "группа",
    "supergroup": "супергруппа",
    "channel": "канал",
}


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    chat = message.chat
    chat_type = CHAT_TYPES.get(chat.type, chat.type)

    lines = [
        "🆔 <b>Данные чата</b>\n",
        f"ID чата: <code>{chat.id}</code>",
        f"Тип: {chat_type}",
    ]
    if chat.title:
        lines.append(f"Название: {chat.title}")
    if message.from_user:
        lines.append(f"\nВаш личный ID: <code>{message.from_user.id}</code>")

    lines.append(
        "\n<i>Скопируйте ID чата в переменную ADMIN_CHAT_ID "
        "файла .env и перезапустите бота.</i>"
    )

    await message.answer("\n".join(lines))
    logger.info("Запрошен ID чата: %s (%s)", chat.id, chat.type)


# ══════════════════════════════════════════════════════════════════
#  Команды для администраторов
#
#  IsAdminChat() вторым аргументом = «И команда, И админский чат».
#  Фильтры перечисляются через запятую и объединяются по И.
# ══════════════════════════════════════════════════════════════════
@router.message(Command("stats"), IsAdminChat())
async def cmd_stats(message: Message, orders: OrderRepository) -> None:
    """Сводка: сколько заявок и в каком они состоянии."""
    by_status = await orders.count_by_status()
    total = sum(by_status.values())

    now = utc_now()
    today = await orders.count_since(now - timedelta(days=1))
    week = await orders.count_since(now - timedelta(days=7))

    if total == 0:
        await message.answer("📊 Заявок пока нет.")
        return

    lines = [
        "📊 <b>Статистика заявок</b>\n",
        f"Всего: <b>{total}</b>",
        f"За сутки: {today}",
        f"За неделю: {week}\n",
    ]
    for status, label in STATUS_LABELS.items():
        count = by_status.get(status, 0)
        # Нулевые статусы показываем только если они значимы:
        # «0 не выгружены» — полезная информация, «0 в таблице» — шум.
        if count or status == OrderStatus.FAILED.value:
            lines.append(f"{label}: {count}")

    if by_status.get(OrderStatus.FAILED.value):
        lines.append(
            "\n⚠️ Есть заявки, не попавшие в таблицу. "
            "Данные сохранены в базе бота, но выгрузка не удалась."
        )

    await message.answer("\n".join(lines))


@router.message(Command("orders"), IsAdminChat())
async def cmd_orders(
    message: Message, orders: OrderRepository, catalog: Catalog
) -> None:
    """Последние заявки с контактами — чтобы не открывать таблицу."""
    recent = await orders.list_recent(limit=RECENT_LIMIT)

    if not recent:
        await message.answer("Заявок пока нет.")
        return

    tz = catalog.school.tzinfo
    lines = [f"📋 <b>Последние заявки</b> (до {RECENT_LIMIT})\n"]

    for order in recent:
        # Время в базе в UTC — показываем в поясе школы.
        created = order.created_at.astimezone(tz).strftime("%d.%m %H:%M")
        mark = "⚠️ " if order.status == OrderStatus.FAILED else ""
        lines.append(
            # escape на данных клиента: имя вида «<b>Аня» иначе сломает
            # HTML-разметку, и Telegram отвергнет сообщение целиком.
            f"{mark}<b>№{order.id}</b> · {created}\n"
            f"{escape(order.name)} · <code>{format_phone_display(order.phone)}</code>\n"
            f"{escape(order.direction_title)}, "
            f"{FOR_WHOM_LABELS.get(order.for_whom, order.for_whom)}\n"
            f"🕐 {escape(order.preferred_time)}"
        )

    await message.answer("\n\n".join(lines))
