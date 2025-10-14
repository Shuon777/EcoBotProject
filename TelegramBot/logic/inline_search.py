# Файл: TelegramBot/logic/inline_search.py

import logging
from typing import List
from pathlib import Path

logger = logging.getLogger(__name__)

# --- НАЧАЛО НОВОЙ ЛОГИКИ ЗАГРУЗКИ ---

def _load_names_from_file() -> List[str]:
    """
    Загружает список названий из текстового файла names.txt.
    Ожидает, что файл находится в корневой директории TelegramBot.
    """
    # Строим путь к файлу относительно текущего файла
    # __file__ -> .../TelegramBot/logic/inline_search.py
    # .parent -> .../TelegramBot/logic/
    # .parent -> .../TelegramBot/
    file_path = Path(__file__).parent.parent / "names.txt"
    
    if not file_path.exists():
        logger.error(f"Файл с названиями не найден по пути: {file_path}. Инлайн-поиск не будет работать.")
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            # Читаем все строки, убираем пробелы по краям и отбрасываем пустые строки
            names = [line.strip() for line in f if line.strip()]
        
        logger.info(f"Успешно загружено {len(names)} названий из {file_path} для инлайн-поиска.")
        return names
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {file_path}: {e}", exc_info=True)
        return []

# Загружаем названия ОДИН РАЗ при старте бота
KNOWN_OBJECTS_ORIGINAL = _load_names_from_file()
# Создаем версию в нижнем регистре для быстрого поиска без учета регистра
KNOWN_OBJECTS_LOWER = [obj.lower() for obj in KNOWN_OBJECTS_ORIGINAL]

# --- КОНЕЦ НОВОЙ ЛОГИКИ ЗАГРУЗКИ ---


# Стартовые подсказки, которые видит пользователь, если ничего не ввел
DEFAULT_SUGGESTIONS = [
    "Байкальская нерпа",
    "Копеечник зундукский",
    "Эдельвейс",
    "Ольхонская полевка"
]

def find_suggestions(query: str, limit: int = 5) -> List[str]:
    """
    Быстро находит релевантные названия объектов по текстовому запросу.
    """
    if not query:
        return DEFAULT_SUGGESTIONS

    query_lower = query.lower()
    matches = []
    
    # Поиск по вхождению строки - простой и очень быстрый
    for i, obj_lower in enumerate(KNOWN_OBJECTS_LOWER):
        if query_lower in obj_lower:
            matches.append(KNOWN_OBJECTS_ORIGINAL[i])
            if len(matches) >= limit:
                break
    
    return matches