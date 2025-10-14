# Файл: TelegramBot/utils/logging_config.py

import logging
from logging.handlers import TimedRotatingFileHandler
import sys

LOG_FILE_PATH = "bot.log"
UNHANDLED_QUERIES_LOG_PATH = "unhandled_queries.log" # Имя нового файла

def setup_logging():
    """Настраивает логирование в файл с ротацией и в консоль."""
    
    # --- Основной логгер (без изменений) ---
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)
    file_handler = TimedRotatingFileHandler(
        LOG_FILE_PATH, 
        when='midnight', 
        interval=1, 
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    # --- Конец основного логгера ---

    # --- НАЧАЛО НОВОГО БЛОКА ---
    # Создаем и настраиваем специальный логгер для нераспознанных запросов
    unhandled_logger = logging.getLogger("unhandled")
    unhandled_logger.setLevel(logging.INFO)
    unhandled_logger.propagate = False # Важно! Чтобы сообщения не дублировались в root_logger
    
    # Формат для этого логгера будет проще: только дата и сообщение
    unhandled_formatter = logging.Formatter('%(asctime)s - %(message)s')
    
    unhandled_file_handler = logging.FileHandler(UNHANDLED_QUERIES_LOG_PATH, encoding='utf-8')
    unhandled_file_handler.setFormatter(unhandled_formatter)
    unhandled_logger.addHandler(unhandled_file_handler)
    # --- КОНЕЦ НОВОГО БЛОКА ---

    logging.info("Логирование настроено для вывода в консоль и в файл.")
    logging.info(f"Нераспознанные запросы будут сохраняться в: {UNHANDLED_QUERIES_LOG_PATH}")