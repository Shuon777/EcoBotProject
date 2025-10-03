# --- ФАЙЛ: utils/logging_config.py ---

import logging
from logging.handlers import TimedRotatingFileHandler
import sys

LOG_FILE_PATH = "bot.log" # Имя файла логов, он появится в папке TelegramBot

def setup_logging():
    """Настраивает логирование в файл с ротацией и в консоль."""
    
    # Создаем основной логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO) # Устанавливаем минимальный уровень логов

    # --- Форматтер: как будут выглядеть строки лога ---
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # --- Обработчик 1: Вывод в консоль (stdout) ---
    # Оставляем его, чтобы видеть логи в реальном времени, например, через journalctl
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # --- Обработчик 2: Запись в файл с ротацией ---
    # TimedRotatingFileHandler - идеальный выбор для долго работающих приложений.
    # when='midnight' - новый файл будет создаваться каждую полночь.
    # backupCount=7 - будет храниться 7 старых файлов логов (за последнюю неделю).
    file_handler = TimedRotatingFileHandler(
        LOG_FILE_PATH, 
        when='midnight', 
        interval=1, 
        backupCount=7,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.info("Логирование настроено для вывода в консоль и в файл.")