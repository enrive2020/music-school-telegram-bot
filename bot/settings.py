"""Настройки приложения, прочитанные из .env.

Почему класс, а не os.getenv() россыпью по коду:
- все настройки объявлены в ОДНОМ месте и с типами;
- если в .env чего-то не хватает или там мусор — бот честно падает
  на старте с понятной ошибкой, а не в случайный момент посреди
  диалога с клиентом. Ошибку конфигурации нужно узнавать при запуске.
"""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Всё, что бот берёт из окружения.

    pydantic-settings сам сопоставляет поле bot_token с переменной
    BOT_TOKEN из .env (регистр не важен) и проверяет тип.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # В .env уже лежат переменные будущих фаз (GOOGLE_SHEET_ID и т.п.).
        # Мы их ещё не объявили — просим pydantic не ругаться на «лишние».
        extra="ignore",
    )

    bot_token: str

    # Путь к YAML-каталогу направлений. Значение по умолчанию есть,
    # поэтому переменная CATALOG_PATH в .env не обязательна.
    catalog_path: Path = Path("config/school.yaml")

    # Чат администраторов, куда бот шлёт новые заявки.
    # None = уведомления выключены (бот при этом полностью работоспособен).
    # У групп ID отрицательный — узнать его можно командой /chatid.
    admin_chat_id: int | None = None

    # Файл локальной базы заявок.
    database_path: Path = Path("data/orders.db")

    # ── Логирование ──
    log_level: str = "INFO"
    log_file: Path | None = Path("logs/bot.log")

    # ── Google Sheets ──
    # JSON-ключ сервисного аккаунта. Секрет: лежит в secrets/, в git не попадает.
    google_credentials_file: Path = Path("secrets/google-credentials.json")
    # ID таблицы — кусок URL между /d/ и /edit.
    # Пустая строка = выгрузка выключена, бот работает только на SQLite.
    google_sheet_id: str = ""

    # Пустая строка в .env (ADMIN_CHAT_ID=) означает «не задано».
    # Без этого pydantic пытается превратить "" в число и падает,
    # хотя незаполненная необязательная переменная — норма:
    # именно так выглядит свежескопированный .env.example.
    @field_validator("admin_chat_id", "log_file", mode="before")
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("bot_token")
    @classmethod
    def _token_not_empty(cls, value: str) -> str:
        # Пустая строка формально проходит проверку типа str,
        # поэтому проверяем осмысленность отдельно.
        if not value.strip():
            raise ValueError(
                "BOT_TOKEN пуст. Открой файл .env и вставь токен, "
                "который выдал @BotFather."
            )
        return value.strip()


def load_settings() -> Settings:
    """Читает .env и возвращает провалидированные настройки."""
    return Settings()
