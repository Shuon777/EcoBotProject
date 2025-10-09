# --- НАЧАЛО ФАЙЛА TelegramBot/config.py ---
import os
from dotenv import load_dotenv
import logging

# Загружаем переменные из .env файла
load_dotenv()
logger = logging.getLogger(__name__)

def get_env_var(var_name: str, default_value: str = None) -> str:
    """Получает переменную окружения или вызывает ошибку, если она не установлена."""
    value = os.getenv(var_name, default_value)
    if value is None:
        error_msg = f"Критическая ошибка: Переменная окружения '{var_name}' не установлена!"
        logger.critical(error_msg)
        raise ValueError(error_msg)
    return value

# --- Telegram Bot ---
BOT_TOKEN = get_env_var("BOT_TOKEN")

# --- Rasa ---
RASA_WEBHOOK_URL = get_env_var("RASA_WEBHOOK_URL", "http://localhost:5006/webhooks/rest/webhook")

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
    "find_geo_special_description": f"{ECOBOT_API_BASE_URL}/object/description/"
}

# --- Таймауты ---
DEFAULT_TIMEOUT = 25
GIGACHAT_TIMEOUT = 50
# --- КОНЕЦ ФАЙЛА TelegramBot/config.py ---