# --- НАЧАЛО ФАЙЛА TelegramBot/settings_manager.py ---
import json
import logging
from pathlib import Path
from filelock import FileLock, Timeout
from typing import Dict, Any
from config import USER_SETTINGS_PATH, USER_LOCK_SETTINGS_PATH

logger = logging.getLogger(__name__)

SETTINGS_FILE_PATH = Path(USER_SETTINGS_PATH)
LOCK_FILE_PATH = Path(USER_LOCK_SETTINGS_PATH)

def get_user_settings(user_id: str) -> Dict[str, Any]:
    """
    Безопасно читает настройки для конкретного пользователя.
    Возвращает словарь с настройками или пустой словарь в случае ошибки.
    """
    user_id = str(user_id)
    try:
        if not SETTINGS_FILE_PATH.exists():
            return {}
        
        with FileLock(LOCK_FILE_PATH, timeout=5):
            with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings.get(user_id, {})
    except (json.JSONDecodeError, Timeout) as e:
        logger.error(f"Ошибка чтения файла настроек: {e}")
        return {}
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при чтении настроек: {e}", exc_info=True)
        return {}

def update_user_settings(user_id: str, new_settings: Dict[str, Any]):
    """
    Безопасно обновляет и сохраняет настройки для одного пользователя.
    """
    user_id = str(user_id)
    try:
        with FileLock(LOCK_FILE_PATH, timeout=10):
            all_settings = {}
            if SETTINGS_FILE_PATH.exists():
                with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as f:
                    try:
                        all_settings = json.load(f)
                    except json.JSONDecodeError:
                        logger.warning("Файл настроек поврежден, будет создан новый.")
                        all_settings = {}

            if user_id not in all_settings:
                all_settings[user_id] = {}
            all_settings[user_id].update(new_settings)

            with open(SETTINGS_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(all_settings, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Настройки для пользователя {user_id} обновлены: {new_settings}")

    except Timeout:
        logger.error("Не удалось получить блокировку для записи файла настроек. Операция отменена.")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при обновлении настроек: {e}", exc_info=True)

# --- КОНЕЦ ФАЙЛА TelegramBot/settings_manager.py ---