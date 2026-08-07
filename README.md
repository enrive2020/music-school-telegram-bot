# Telegram-бот приёма заявок — «Школа музыки»

Бот для образовательного центра: клиент выбирает направление обучения,
оставляет контакты и удобное время, заявка сохраняется и уходит
в Google Sheets, администраторы получают уведомление в Telegram.

> 🚧 Проект в разработке. Подробный README — в финальной фазе.

## Стек

Python 3.12 · aiogram 3 · SQLite · Google Sheets API · pytest

## Быстрый старт

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env   # затем заполнить значения
.venv\Scripts\python.exe -m bot.main
```

## Структура

```
bot/          код бота (handlers, keyboards, states, storage, services)
config/       конфиг направлений обучения (YAML) — правится без программиста
tests/        тесты
data/         локальная база заявок (не в git)
logs/         логи (не в git)
```
