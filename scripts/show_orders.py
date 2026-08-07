"""Утилита разработчика: показать заявки из локальной базы.

Запуск:
    .venv\\Scripts\\python.exe -m scripts.show_orders

Нужна, чтобы глазами проверить, что бот действительно сохраняет
данные, не открывая базу сторонними программами.
"""

import asyncio

from bot.settings import load_settings
from bot.storage.database import connect, init_schema
from bot.storage.orders import OrderRepository

# Человеческие подписи — те же, что показывает бот.
FOR_WHOM = {"child": "ребёнок", "adult": "взрослый"}


async def main() -> None:
    settings = load_settings()
    conn = await connect(settings.database_path)
    # На случай, если бота ещё не запускали и таблиц нет:
    # CREATE TABLE IF NOT EXISTS ничего не сломает, если они уже есть.
    await init_schema(conn)
    repo = OrderRepository(conn)

    counts = await repo.count_by_status()
    print("Сводка по статусам:", counts or "база пуста")

    orders = await repo.list_recent(limit=20)
    if not orders:
        print("\nЗаявок пока нет.")
    for o in orders:
        # Время в базе в UTC — переводим в местный пояс для чтения.
        created = o.created_at.astimezone().strftime("%d.%m.%Y %H:%M")
        print(f"\n─── Заявка №{o.id} [{o.status.value}] ───")
        print(f"  Создана:     {created}")
        print(f"  Направление: {o.direction_title} ({o.price_per_lesson} ₽)")
        print(f"  Для кого:    {FOR_WHOM.get(o.for_whom, o.for_whom)}")
        print(f"  Имя:         {o.name}")
        print(f"  Телефон:     {o.phone}")
        print(f"  Время:       {o.preferred_time}")
        print(f"  Комментарий: {o.comment or '—'}")
        print(f"  Telegram:    id={o.telegram_user_id} @{o.telegram_username or '-'}")
        if o.last_error:
            print(f"  Ошибка синхр.: {o.last_error}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
