"""Проверка доступа к Google Sheets — до написания кода синхронизации.

Запуск:
    .venv\\Scripts\\python.exe -m scripts.check_sheets

Смысл: настройка доступов Google — самая частая точка отказа во всей
затее. Проверять её сквозь слой фоновых задач мучительно, поэтому
проверяем отдельно и с понятными сообщениями о каждой причине отказа.

Скрипт ничего не портит: создаёт временный лист, пишет в него
и тут же удаляет.
"""

import json
import sys

import gspread
from google.auth.exceptions import GoogleAuthError

from bot.settings import load_settings

TEMP_SHEET = "__проверка_доступа__"


def fail(message: str, hint: str = "") -> None:
    """Печатает причину отказа и выходит с ненулевым кодом."""
    print(f"\n❌ {message}")
    if hint:
        print(f"   → {hint}")
    sys.exit(1)


def main() -> None:
    settings = load_settings()

    # ── 1. Файл ключа ──
    key_path = settings.google_credentials_file
    print(f"1. Ключ: {key_path}")
    if not key_path.exists():
        fail(
            f"файла {key_path.resolve()} нет",
            "Скачай JSON-ключ сервисного аккаунта и положи его сюда. "
            "Проверь, что расширение не задвоилось (.json.json).",
        )

    try:
        key_data = json.loads(key_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"файл ключа — не корректный JSON: {e}", "Скачай ключ заново.")

    client_email = key_data.get("client_email")
    if not client_email:
        fail(
            "в файле нет поля client_email",
            "Похоже, это не ключ сервисного аккаунта, а другой файл "
            "(например, OAuth-клиент). Нужен ключ типа «Service account».",
        )
    print(f"   робот: {client_email}")

    # ── 2. ID таблицы ──
    sheet_id = settings.google_sheet_id.strip()
    print(f"\n2. ID таблицы: {sheet_id or '(пусто)'}")
    if not sheet_id:
        fail(
            "GOOGLE_SHEET_ID в .env пуст",
            "Скопируй кусок URL таблицы между /d/ и /edit.",
        )
    if "/" in sheet_id or "docs.google.com" in sheet_id:
        fail(
            "в GOOGLE_SHEET_ID записан весь URL, а нужен только ID",
            "Оставь кусок между /d/ и /edit, без https:// и без /edit.",
        )

    # ── 3. Аутентификация ──
    print("\n3. Подключение к Google…")
    try:
        client = gspread.service_account(filename=str(key_path))
    except GoogleAuthError as e:
        fail(f"Google отверг ключ: {e}", "Создай новый ключ в консоли Google Cloud.")

    # ── 4. Открытие таблицы ──
    print("4. Открываю таблицу…")
    try:
        spreadsheet = client.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        fail(
            "таблица не найдена или робот к ней не допущен",
            f"Открой таблицу → «Поделиться» → добавь {client_email} "
            "с правами «Редактор». Ещё проверь, что ID скопирован верно.",
        )
    except gspread.exceptions.APIError as e:
        # Самая частая причина здесь — не включённый API.
        fail(
            f"Google вернул ошибку API: {e}",
            "Проверь, что в проекте Google Cloud включены "
            "Google Sheets API и Google Drive API.",
        )
    print(f"   название: «{spreadsheet.title}»")
    print(f"   листов: {[ws.title for ws in spreadsheet.worksheets()]}")

    # ── 5. Права на запись ──
    print("\n5. Проверяю право записи…")
    try:
        temp = spreadsheet.add_worksheet(title=TEMP_SHEET, rows=2, cols=2)
    except gspread.exceptions.APIError as e:
        fail(
            f"не удалось создать лист: {e}",
            f"Скорее всего у {client_email} права «Читатель». "
            "Нужен «Редактор».",
        )

    try:
        temp.update_acell("A1", "проверка")
        value = temp.acell("A1").value
        print(f"   записал и прочитал: {value!r}")
    finally:
        # Убираем за собой в любом случае, даже если запись упала.
        spreadsheet.del_worksheet(temp)
        print("   временный лист удалён")

    print("\n✅ Доступ есть: чтение, запись и удаление листов работают.")


if __name__ == "__main__":
    main()
