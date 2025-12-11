# --- НАЧАЛО ФАЙЛА RasaProject/actions/logic/config_rasa.py ---
import os
import logging
from dotenv import load_dotenv

# Загружаем переменные из .env файла, который должен быть доступен сервису Rasa
load_dotenv()
logger = logging.getLogger(__name__)

def get_env_var(var_name: str, default_value: str = None) -> str:
    """Получает переменную окружения или использует значение по умолчанию."""
    value = os.getenv(var_name, default_value)
    if value is None:
        # Для сервера действий мы не будем падать, а просто залогируем предупреждение
        logger.warning(f"Переменная окружения '{var_name}' не установлена! Используется None.")
    return value

# --- GigaChat Fallback API ---
GIGACHAT_FALLBACK_URL = get_env_var("GIGACHAT_FALLBACK_URL", "http://localhost:5556/ask_simple")

# --- Backend EcoBot API (testecobot.ru) ---
ECOBOT_API_BASE_URL = get_env_var("ECOBOT_API_BASE_URL", "https://testecobot.ru")

API_URLS = {
    "get_description": f"{ECOBOT_API_BASE_URL}/api/species/description/",
    "search_images": f"{ECOBOT_API_BASE_URL}/search_images_by_features",
    "get_coords": f"{ECOBOT_API_BASE_URL}/api/get_coords",
    "coords_to_map": f"{ECOBOT_API_BASE_URL}/api/coords_to_map",
    "objects_in_polygon": f"{ECOBOT_API_BASE_URL}/api/objects_in_polygon_simply",
    "ask_ecobot": f"{ECOBOT_API_BASE_URL}/api/ask",
    "is_known_object": f"{ECOBOT_API_BASE_URL}/api/is_known_object",
    "get_species_area": f"{ECOBOT_API_BASE_URL}/api/get_species_area",
    "draw_multiple_places": f"{ECOBOT_API_BASE_URL}/api/draw_multiple_places",
    "find_species_with_description": f"{ECOBOT_API_BASE_URL}/api/find_species_with_description",
}

# --- Путь к файлу настроек ---
USER_SETTINGS_PATH=get_env_var("USER_SETTINGS_PATH")
USER_LOCK_SETTINGS_PATH=get_env_var("USER_LOCK_SETTINGS_PATH")

# --- Таймауты ---
DEFAULT_TIMEOUT = 25
GIGACHAT_TIMEOUT = 50
# --- КОНЕЦ ФАЙЛА RasaProject/actions/logic/config_rasa.py ---