# --- НАЧАЛО ФАЙЛА RasaProject/logic/slot_validator.py ---

from typing import List, Dict, Optional
from rasa_sdk import Tracker
from rasa_sdk.executor import CollectingDispatcher
from pathlib import Path
import requests
import pymorphy2
import json
import logging

# Настраиваем логирование
logger = logging.getLogger(__name__)

morph = pymorphy2.MorphAnalyzer()

def normalize_yo(text: str) -> str:
    """Заменяет ё на е и приводит к нижнему регистру"""
    if not text:
        return ""
    return text.lower().replace('ё', 'е')

def normalize_to_nominative(text: str) -> str:
    """Приводит каждое слово к именительному падежу и нижнему регистру"""
    if not text:
        return ""
    
    logger.debug(f"Начало нормализации для текста: '{text}'")
    try:
        words = text.split()
        result = []
        for word in words:
            parsed = morph.parse(word)[0]
            nom = parsed.inflect({'nomn'})
            result.append(nom.word if nom else word)
        
        result_string = " ".join(result).lower()
        logger.debug(f"Текст '{text}' нормализован в '{result_string}'")
        return result_string
    except Exception as e:
        logger.error(f"Ошибка при нормализации текста '{text}': {e}", exc_info=True)
        return text.lower()

def load_objects_from_json() -> List[str]:
    """Загружает объекты из JSON, приводит к нижнему регистру и заменяет ё."""
    json_path = Path(__file__).parent / "names_OFF.json"
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Возвращаем оригинальную, простую логику нормализации списка
            return [normalize_yo(item["name_ru"]) for item in data.get("resource_identifiers", [])]
    except Exception as e:
        logger.error(f"Не удалось загрузить или обработать names_OFF.json: {e}", exc_info=True)
        return []

KNOWN_OBJECTS = load_objects_from_json()

def methods_of_validation(method: str, debug_mode: bool = False) -> bool:
    if method == "json":
        if not KNOWN_OBJECTS:
            if debug_mode:
                logger.warning("Проверка по JSON невозможна: список известных объектов пуст.")
            return False
        return True
    elif method == "db_api":
        try:
            test_response = requests.get("https://testecobot.ru/api/", timeout=5)
            if debug_mode:
                logger.debug(f"Проверка доступности API: status_code={test_response.status_code}")
            return test_response.ok
        except requests.exceptions.RequestException as e:
            if debug_mode:
                logger.warning(f"API недоступно: {e}")
            return False
    return False

def fuzzy_match(normalized_input: str, known_objects: List[str]) -> Optional[str]:
    """
    Пытается найти хотя бы частичное совпадение с известными объектами.
    (ВАША ОРИГИНАЛЬНАЯ, РАБОТАЮЩАЯ ВЕРСИЯ)
    """
    for known in known_objects:
        if normalized_input in known or known in normalized_input:
            return known
    return None

def handle_known_object_check(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    object: str,
    validation_method: str = "json", # Временно используем только JSON
    debug_mode: bool = False
) -> bool:
    if not object:
        dispatcher.utter_message(text="Пожалуйста, укажите объект.")
        return False

    normalized_object = normalize_yo(normalize_to_nominative(object))
    logger.debug(f"Проверка объекта. Исходный: '{object}', нормализованный: '{normalized_object}'")
    
    if debug_mode:
        dispatcher.utter_message(text=f"[DEBUG] Нормализованный объект: {normalized_object}")
        dispatcher.utter_message(text=f"[DEBUG] Всего объектов в базе: {len(KNOWN_OBJECTS)}")

    # Попытка локальной проверки
    if validation_method in ("json", "auto") and methods_of_validation("json", debug_mode):
        # Точное совпадение
        if normalized_object in KNOWN_OBJECTS:
            logger.debug(f"Объект '{normalized_object}' найден в локальной базе (точное совпадение).")
            if debug_mode: dispatcher.utter_message(text=f"[DEBUG] Объект найден в базе.")
            return True

        # Поиск по подстроке
        fuzzy_found = fuzzy_match(normalized_object, KNOWN_OBJECTS)
        if fuzzy_found:
            logger.debug(f"Объект '{normalized_object}' найден в локальной базе (совпадение по подстроке с '{fuzzy_found}').")
            if debug_mode: dispatcher.utter_message(text=f"[DEBUG] Найден по подстроке: {fuzzy_found}")
            return True

        if validation_method == "json":
            dispatcher.utter_message(text="К сожалению, я не знаю такого растения или животного.")
            return False

    # Попытка проверки через API
    if validation_method in ("db_api", "auto") and methods_of_validation("db_api", debug_mode):
        logger.debug(f"Объект не найден локально, обращаюсь к API: testecobot.ru/api/is_known_object")
        try:
            response = requests.post("https://testecobot.ru/api/is_known_object", json={"object": normalized_object}, timeout=10)
            
            logger.debug(f"API is_known_object ответило со статусом: {response.status_code}")
            if response.status_code == 200 and response.text:
                data = response.json()
                logger.debug(f"Ответ от API: {data}")
                if debug_mode: dispatcher.utter_message(text=f"[DEBUG] Ответ от API: {data}")
                if data.get("known") is True or str(data.get("known")) == "ambiguous":
                    return True
            else:
                logger.error(f"API is_known_object вернуло невалидный ответ. Статус: {response.status_code}, Тело ответа: '{response.text}'")
                return False 
                
        except requests.exceptions.JSONDecodeError as e:
            logger.error(f"Ошибка декодирования JSON. Ответ сервера: '{response.text}'. Ошибка: {e}", exc_info=True)
            if debug_mode: dispatcher.utter_message(text=f"[DEBUG] API вернуло не-JSON ответ: {response.text}")
            return False
        except Exception as e:
            logger.error(f"Ошибка при обращении к API проверки объекта: {e}", exc_info=True)
            if debug_mode: dispatcher.utter_message(text=f"Произошла ошибка при проверке объекта: {e}")
            return False

    logger.warning(f"Объект '{normalized_object}' не прошел проверку ни одним из методов.")
    dispatcher.utter_message(text="К сожалению, я не знаю такого растения или животного.")
    return False

# --- КОНЕЦ ФАЙЛА RasaProject/logic/slot_validator.py ---