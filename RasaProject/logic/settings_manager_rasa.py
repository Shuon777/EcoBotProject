import json
import logging
from pathlib import Path
from filelock import FileLock, Timeout
from typing import Dict, Any
from .config_rasa import USER_SETTINGS_PATH 

logger = logging.getLogger(__name__)

if not USER_SETTINGS_PATH:
    logger.critical("Путь к файлу настроек (USER_SETTINGS_PATH) не задан в .env!")
    SETTINGS_FILE_PATH = None
    LOCK_FILE_PATH = None
else:
    SETTINGS_FILE_PATH = Path(USER_SETTINGS_PATH)
    LOCK_FILE_PATH = SETTINGS_FILE_PATH.with_suffix(".json.lock")

def get_user_settings(user_id: str) -> Dict[str, Any]:
    """
    Безопасно читает настройки для конкретного пользователя.
    """
    if not SETTINGS_FILE_PATH:
        logger.error("Не могу прочитать настройки: путь к файлу не сконфигурирован.")
        return {}

    user_id = str(user_id)
    try:
        if not SETTINGS_FILE_PATH.exists():
            logger.warning(f"Файл настроек не найден по пути: {SETTINGS_FILE_PATH}")
            return {}
        
        with FileLock(LOCK_FILE_PATH, timeout=5):
            with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
                user_data = settings.get(user_id, {})
                logger.debug(f"Настройки для user_id={user_id} успешно прочитаны: {user_data}")
                return user_data
    except Timeout:
        logger.error(f"Не удалось получить блокировку файла настроек: {LOCK_FILE_PATH}")
        return {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Ошибка чтения или парсинга файла настроек {SETTINGS_FILE_PATH}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при чтении настроек: {e}", exc_info=True)
        return {}
# --- КОНЕЦ ФАЙЛА ---