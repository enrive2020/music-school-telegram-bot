"""Настройки приложения, прочитанные из .env.

Почему класс, а не os.getenv() россыпью по коду:
- все настройки объявлены в ОДНОМ месте и с типами;
- если в .env чего-то не хватает или там мусор — бот честно падает
  на старте с понятной ошибкой, а не в случайный момент посреди
  диалога с клиентом. Ошибку конфигурации нужно узнавать при запуске.
"""

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
