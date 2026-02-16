import logging
import os
from logging.handlers import RotatingFileHandler

_configured = False


def configure_logging(log_file_path: str) -> logging.Logger:
    """Configure a rotating file + console logger for the bot."""
    global _configured
    logger = logging.getLogger("feed_bot")
    if _configured:
        return logger

    log_dir = os.path.dirname(log_file_path) or "."
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S%z",
    )

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    _configured = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    base = "feed_bot"
    if name:
        return logging.getLogger(f"{base}.{name}")
    return logging.getLogger(base)
