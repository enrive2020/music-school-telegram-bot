"""Мини-инфраструктура для тестирования бота без настоящего Telegram.

Идея: подменяем только ТРАНСПОРТ. Bot, Dispatcher, роутеры, фильтры
и FSM работают по-настоящему — то есть тест проверяет реальную
маршрутизацию и переходы состояний, а не их имитацию.

FakeSession перехватывает исходящие вызовы API, запоминает их
и возвращает правдоподобные ответы. После прогона апдейта можно
спросить: какие сообщения бот отправил, что было в кнопках,
в каком состоянии остался клиент.
"""

from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import CallbackQuery, Chat, Message, Update, User

USER_ID = 555_444_333
CHAT_ID = 555_444_333

TEST_USER = User(id=USER_ID, is_bot=False, first_name="Тест")
TEST_CHAT = Chat(id=CHAT_ID, type="private")


class FakeSession(BaseSession):
    """Сессия, которая никуда не ходит, а копит вызовы."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[TelegramMethod] = []
        self._next_message_id = 100

    async def close(self) -> None:
        pass

    async def make_request(
        self, bot: Bot, method: TelegramMethod, timeout: int | None = None
    ) -> Any:
        self.sent.append(method)
        return self._fake_result(method, bot)

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""

    def _fake_result(self, method: TelegramMethod, bot: Bot) -> Any:
        """Правдоподобный ответ Telegram на вызов.

        Возвращаемый тип метод объявляет сам — берём его и решаем,
        что подсунуть: Message или True.
        """
        returning = method.__returning__

        # bool-методы: answerCallbackQuery, deleteMessage и подобные.
        if returning is bool:
            return True

        # Методы, отдающие Message (sendMessage, editMessageText…).
        # Union «Message | bool» тоже покрываем — отдаём Message.
        self._next_message_id += 1
        message = Message(
            message_id=self._next_message_id,
            date=datetime.now(timezone.utc),
            chat=TEST_CHAT,
            from_user=TEST_USER,
            text=getattr(method, "text", None) or "",
        )
        # as_(bot) обязательно: без привязки к боту у объекта не работают
        # методы вроде message.delete() — они не знают, куда слать запрос.
        # Настоящий Telegram отдаёт уже привязанные объекты.
        return message.as_(bot)

    # ── Удобные выборки для проверок ──
    def texts(self) -> list[str]:
        """Тексты отправленных и отредактированных СООБЩЕНИЙ.

        AnswerCallbackQuery исключён намеренно: у него тоже есть поле
        text, но это всплывающая подсказка на кнопке, а не сообщение
        в чате. Для неё отдельный метод alerts().
        """
        return [
            t
            for m in self.sent
            if type(m).__name__ != "AnswerCallbackQuery"
            and (t := getattr(m, "text", None))
        ]

    def last_text(self) -> str:
        assert self.texts(), "бот не отправил ни одного сообщения"
        return self.texts()[-1]

    def buttons(self) -> list[str]:
        """Подписи кнопок последней отправленной клавиатуры."""
        for method in reversed(self.sent):
            markup = getattr(method, "reply_markup", None)
            rows = getattr(markup, "inline_keyboard", None)
            if rows:
                return [b.text for row in rows for b in row]
        return []

    def callback_data(self) -> list[str]:
        """callback_data последней отправленной клавиатуры."""
        for method in reversed(self.sent):
            markup = getattr(method, "reply_markup", None)
            rows = getattr(markup, "inline_keyboard", None)
            if rows:
                return [b.callback_data for row in rows for b in row if b.callback_data]
        return []

    def alerts(self) -> list[str]:
        """Тексты всплывающих ответов на нажатие кнопки."""
        return [
            m.text
            for m in self.sent
            if type(m).__name__ == "AnswerCallbackQuery" and getattr(m, "text", None)
        ]

    def clear(self) -> None:
        self.sent.clear()


def make_message(text: str, message_id: int = 1, chat_id: int | None = None) -> Update:
    """Апдейт «пользователь прислал текст».

    chat_id задаётся при создании: объекты aiogram — неизменяемые
    pydantic-модели, поменять chat.id после конструирования нельзя.
    """
    chat = TEST_CHAT if chat_id is None else Chat(id=chat_id, type="supergroup")
    return Update(
        update_id=message_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=TEST_USER,
            text=text,
        ),
    )


def make_callback(data: str, message_id: int = 1) -> Update:
    """Апдейт «пользователь нажал инлайн-кнопку»."""
    return Update(
        update_id=message_id + 1000,
        callback_query=CallbackQuery(
            id=f"cb{message_id}",
            from_user=TEST_USER,
            chat_instance="test-instance",
            data=data,
            message=Message(
                message_id=message_id,
                date=datetime.now(timezone.utc),
                chat=TEST_CHAT,
                from_user=TEST_USER,
                text="предыдущий экран",
            ),
        ),
    )
