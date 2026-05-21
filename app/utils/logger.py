"""Настройка логирования"""

import logging
import sys


def setup_logger(level: str = "INFO") -> None:
    """Настройка глобального логгера приложения"""
    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Избегаем дублирования хэндлеров
    if not root_logger.handlers:
        root_logger.addHandler(handler)

    # Приглушаем слишком шумные библиотеки
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
