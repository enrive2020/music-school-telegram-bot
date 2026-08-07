"""Настройка логирования: консоль + файл с ротацией.

Почему не хватает logging.basicConfig в консоль:
  • закрыл терминал — история потеряна;
  • на сервере бот работает без терминала вообще;
  • «вчера вечером что-то сломалось» невозможно расследовать.

Почему с ротацией: лог растёт бесконечно и однажды займёт весь диск.
RotatingFileHandler режет файл по размеру и хранит N последних кусков.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 5 МБ на файл, 5 файлов в истории → максимум 30 МБ на диске.
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Настраивает корневой логгер: вывод в консоль и, если задан, в файл."""
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(level.upper())
    # Чистим обработчики: иначе при повторном вызове (например, в тестах)
    # каждая строка будет печататься дважды.
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            # encoding обязателен: без него Windows пишет в cp1251
            # и кириллица в логах превращается в мусор.
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # aiohttp на уровне INFO печатает каждый сетевой запрос — при long
    # polling это строка каждые несколько секунд, лог заплывает шумом.
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Логирование настроено: уровень %s, файл %s",
        level.upper(),
        log_file if log_file else "нет",
    )
