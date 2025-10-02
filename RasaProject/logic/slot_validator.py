import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml
import pymorphy2
from rasa_sdk import Tracker
from rasa_sdk.executor import CollectingDispatcher
from thefuzz import fuzz, process

logger = logging.getLogger(__name__)

# --- Инициализация Pymorphy2 ---
try:
    morph = pymorphy2.MorphAnalyzer()
    logger.debug("pymorphy2 успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации pymorphy2: {e}. Функции нормализации могут работать некорректно.", exc_info=True)
    morph = None

# --- Функции нормализации текста ---

def normalize_yo(text: str) -> str:
    """Заменяет ё на е и приводит к нижнему регистру, удаляя пробелы по краям."""
    if not text:
        return ""
    return text.strip().lower().replace('ё', 'е')

def normalize_to_nominative(text: str) -> str:
    """
    Приводит каждое слово в строке к его словарной форме (лемме) и нижнему регистру.
    Использует pymorphy2, с порогом уверенности для распознавания слов.
    """
    if not text:
        return ""
    
    logger.debug(f"Начало нормализации (pymorphy2) для текста: '{text}'")
    if morph is None:
        logger.warning(f"pymorphy2 не инициализирован. Возвращаем '{normalize_yo(text)}' без морфологического анализа.")
        return normalize_yo(text)
    
    try:
        words = normalize_yo(text).split() 
        result = []
        for word in words:
            parsed_word = morph.parse(word)
            if parsed_word and parsed_word[0].score >= 0.5: 
                result.append(parsed_word[0].normal_form)
            else:
                result.append(word) 
        
        result_string = " ".join(result)
        logger.debug(f"Текст '{text}' нормализован (pymorphy2) в '{result_string}'")
        return result_string
    except Exception as e:
        logger.error(f"Ошибка при нормализации (pymorphy2) текста '{text}': {e}", exc_info=True)
        return normalize_yo(text)

# --- Глобальные списки известных объектов ---
KNOWN_OBJECTS_ORIGINAL: List[str] = []
KNOWN_OBJECTS_NORMALIZED: List[str] = []

# --- Функция загрузки канонических названий ---

def load_canonical_object_names():
    """
    Загружает канонические названия объектов из object_off_lookup.yml.
    Заполняет KNOWN_OBJECTS_ORIGINAL (оригинальные, stripped) 
    и KNOWN_OBJECTS_NORMALIZED (normalize_yo версии этих названий).
    """
    global KNOWN_OBJECTS_ORIGINAL, KNOWN_OBJECTS_NORMALIZED
    canonical_names_from_file_set = set()
    lookup_path = Path(__file__).parent.parent / "data" / "object_off_lookup.yml"

    try:
        with open(lookup_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
            if data and 'nlu' in data and isinstance(data['nlu'], list):
                for entry in data['nlu']:
                    if 'lookup' in entry and entry['lookup'] == 'object_OFF' and 'examples' in entry:
                        if isinstance(entry['examples'], str):
                            lines = entry['examples'].splitlines()
                            for line in lines:
                                cleaned_line = line.strip()
                                if cleaned_line.startswith('- '):
                                    item = cleaned_line[2:].strip()
                                    if item:
                                        canonical_names_from_file_set.add(item)
                                elif cleaned_line:
                                    canonical_names_from_file_set.add(cleaned_line)
                        elif isinstance(entry['examples'], list):
                            for example in entry['examples']:
                                cleaned_example = str(example).strip()
                                if cleaned_example:
                                    canonical_names_from_file_set.add(cleaned_example)
                        else:
                            logger.warning(f"Неожиданный формат 'examples' для 'object_OFF': {type(entry['examples'])}. Ожидалась строка или список.")
            
            logger.info(f"Успешно загружено {len(canonical_names_from_file_set)} уникальных наименований из object_off_lookup.yml.")
            if canonical_names_from_file_set:
                first_item = next(iter(canonical_names_from_file_set))
                logger.debug(f"Пример первого загруженного названия: '{first_item}'")
            else:
                logger.warning("object_off_lookup.yml не содержит примеров для 'object_OFF' или их не удалось распарсить.")

    except FileNotFoundError:
        logger.exception(f"ERROR: Файл {lookup_path} не найден. Проверьте путь или наличие файла.")
    except yaml.YAMLError as e:
        logger.exception(f"ERROR: Ошибка при парсинге YAML файла {lookup_path}: {e}")
    except Exception as e:
        logger.exception(f"ERROR: Непредвиденная ошибка при загрузке object_off_lookup.yml: {e}")

    # --- ВРЕМЕННЫЙ КОД ДЛЯ ОТЛАДКИ ПРОБЛЕМЫ "КОПЕЕЧНИКА ЗУНДУКСКОГО" ---
    temp_list_original = sorted(list(canonical_names_from_file_set))
    if "Копеечник зундукский" not in temp_list_original:
        temp_list_original.insert(0, "Копеечник зундукский")
        logger.warning("Диагностика: Принудительно добавлен 'Копеечник зундукский' в KNOWN_OBJECTS_ORIGINAL для отладки.")
    KNOWN_OBJECTS_ORIGINAL = temp_list_original
    # --- КОНЕЦ ВРЕМЕННОГО КОДА ---

    # ТЕСТОВОЕ ИЗМЕНЕНИЕ: ИСПОЛЬЗУЕМ normalize_yo ВМЕСТО normalize_to_nominative для KNOWN_OBJECTS_NORMALIZED
    KNOWN_OBJECTS_NORMALIZED = [normalize_yo(name) for name in KNOWN_OBJECTS_ORIGINAL]
    
    logger.debug(f"Инициализированы списки KNOWN_OBJECTS_ORIGINAL ({len(KNOWN_OBJECTS_ORIGINAL)} элементов) и KNOWN_OBJECTS_NORMALIZED ({len(KNOWN_OBJECTS_NORMALIZED)} элементов).")

    # --- ДОПОЛНИТЕЛЬНОЕ ЛОГИРОВАНИЕ ДЛЯ ДИАГНОСТИКИ ПРОБЛЕМЫ "зундукского" ---
    found_zuk_debug = False
    for i, name_orig in enumerate(KNOWN_OBJECTS_ORIGINAL):
        if normalize_yo("копеечник зундукский") == normalize_yo(name_orig): 
            logger.debug(f"Диагностика: Найден канонический 'копеечник зундукский' в KNOWN_OBJECTS_ORIGINAL[{i}]: '{name_orig}'")
            logger.debug(f"Диагностика: Соответствующий нормализованный (yo): KNOWN_OBJECTS_NORMALIZED[{i}]: '{KNOWN_OBJECTS_NORMALIZED[i]}'")
            found_zuk_debug = True
            break
    if not found_zuk_debug:
        logger.warning("Диагностика: 'Копеечник зундукский' не найден в KNOWN_OBJECTS_ORIGINAL после загрузки, даже после принудительного добавления.")
    # --- КОНЕЦ ДОПОЛНИТЕЛЬНОГО ЛОГИРОВАНИЯ ---

# Вызываем загрузку списков при импорте модуля, чтобы они были готовы к использованию.
load_canonical_object_names()

# --- Функция нечёткого сопоставления ---

def get_canonical_name_with_fuzzy_match(input_object_name: str, threshold: int = 65) -> Optional[str]: 
    if not input_object_name or not KNOWN_OBJECTS_ORIGINAL:
        logger.debug("Входное имя объекта пустое или список известных объектов пуст. Возвращаем None.")
        return None

    normalized_input = normalize_yo(input_object_name)
    logger.debug(f"Входное имя для fuzzy match: '{input_object_name}', нормализованное (yo): '{normalized_input}'")

    best_match_result = None
    best_score = -1
    
    # --- ВРЕМЕННОЕ РЕШЕНИЕ: ПРЯМОЙ ПЕРЕБОР ВМЕСТО process.extractOne ---
    logger.debug("ИСПОЛЬЗУЕМ ПРЯМОЙ ПЕРЕБОР ВМЕСТО process.extractOne")
    
    for i, known_name in enumerate(KNOWN_OBJECTS_NORMALIZED):
        score_ratio = fuzz.ratio(normalized_input, known_name)
        score_token_sort = fuzz.token_sort_ratio(normalized_input, known_name)
        
        # Используем лучший балл из двух методов
        current_score = max(score_ratio, score_token_sort)
        
        if current_score > best_score:
            best_score = current_score
            best_match_result = (known_name, current_score, i)
            logger.debug(f"  - Новый лучший результат: '{known_name}' (индекс {i}) с баллом {current_score}")
            
            # Если нашли идеальное совпадение, можно остановиться раньше
            if current_score >= 95:
                break

    # --- КОНЕЦ ВРЕМЕННОГО РЕШЕНИЯ ---

    if best_match_result:
        matched_normalized_name, score, original_index = best_match_result
        logger.debug(f"Лучший результат прямого перебора: '{matched_normalized_name}' (индекс {original_index}) с баллом {score}")
        
        if score >= threshold:
            logger.debug(f"Успешное fuzzy match! Возвращаем '{KNOWN_OBJECTS_ORIGINAL[original_index]}'")
            return KNOWN_OBJECTS_ORIGINAL[original_index]
    
    logger.debug(f"Не найдено достаточно хорошее fuzzy совпадение для '{input_object_name}'. Лучший балл: {best_score} (пороговое значение: {threshold}).")
    return None

# --- Функция обработки проверки слотов ---
def handle_known_object_check(
    dispatcher: CollectingDispatcher,
    tracker: Tracker,
    object: str,
    debug_mode: bool = False
) -> Optional[str]:
    """
    Проверяет, является ли объект известным, используя нечёткое сопоставление.
    Если объект известен, возвращает его каноническое название.
    Если нет, отправляет сообщение пользователю и возвращает None.
    """
    if not object:
        dispatcher.utter_message(text="Пожалуйста, укажите объект для поиска информации.")
        return None

    if not KNOWN_OBJECTS_ORIGINAL:
        logger.error("Список известных объектов пуст. Проверка невозможна. Убедитесь, что 'object_off_lookup.yml' корректно заполнен и доступен.")
        dispatcher.utter_message(text="Извините, внутренняя ошибка: данные об объектах недоступны. Пожалуйста, сообщите разработчикам.")
        return None

    canonical_object_name = get_canonical_name_with_fuzzy_match(object)
    
    if debug_mode:
        dispatcher.utter_message(text=f"[DEBUG] Исходный объект из слота: '{object}'")
        dispatcher.utter_message(text=f"[DEBUG] Каноническое имя (fuzzy match): '{canonical_object_name}'")
        dispatcher.utter_message(text=f"[DEBUG] Всего известных объектов в базе: {len(KNOWN_OBJECTS_ORIGINAL)}")

    if canonical_object_name:
        logger.debug(f"Объект '{object}' успешно сопоставлен с каноническим '{canonical_object_name}' (fuzzy match).")
        if debug_mode:
            dispatcher.utter_message(text=f"[DEBUG] Объект сопоставлен с: '{canonical_object_name}'")
        return canonical_object_name
    else:
        logger.warning(f"Объект '{object}' не прошел проверку нечётким сопоставлением. Исходный объект: '{object}'.")
        dispatcher.utter_message(text="К сожалению, я не знаю такого растения или животного. Пожалуйста, проверьте название или попробуйте использовать синонимы.")
        return None