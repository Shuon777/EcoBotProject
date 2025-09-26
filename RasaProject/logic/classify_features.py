# --- НАЧАЛО ФАЙЛА RasaProject/logic/classify_features.py ---

import re
from typing import List, Dict, Any, Optional
from .slot_validator import normalize_to_nominative

# --- СЛОВАРИ КАТЕГОРИЙ ---
SEASONS = {
    "Зима": ["зима", "зимой", "зимний", "зимняя", "зимою"],
    "Весна": ["весна", "весной", "весенний", "весенняя"],
    "Лето": ["лето", "летом", "летний", "летняя"],
    "Осень": ["осень", "осенью", "осенний", "осенняя"],
}

CLOUDINESS = {
    "Ясно": ["ясно", "ясный", "ясная", "ясную", "солнечно"],
    "Переменная облачность": ["переменная облачность", "облачно с прояснениями"],
    "Пасмурно": ["пасмурно", "пасмурный", "пасмурная", "пасмурную", "облачно", "тучи"]
}

# --- ОБНОВЛЕННЫЙ СЛОВАРЬ HABITAT С СИНОНИМАМИ ---
HABITAT = {
    "Лес": ["лес", "в лесу", "в тайге", "на опушке"],
    "Поле": ["поле", "в поле"],
    "Луг": ["луг", "на лугу"],
    "Горы": ["горы", "в горах", "на склоне", "на скале", "среди скал"],
    "Городская среда": ["город", "в городе", "в парке"],
    "Побережье": ["побережье", "на берегу", "вдоль берега", "у воды"],
    "Река": ["река", "у реки", "вдоль реки"],
    "Озеро": ["озеро", "на озере"],
    "Болото": ["болото", "на болоте"],
    "На дереве": ["на дереве", "на ветке"],
    "На земле": ["на земле", "в траве", "на траве", "на песке", "на снегу"],
    "В воде": ["в воде", "под водой"],
}

FRUITS = {
    "шишка": "Шишка", "желудь": "Желудь", "ягода": "Ягода",
    "плод": "Плод", "орех": "Орех", "семя": "Семя"
}

FAUNA_TYPE = {"млекопитающее": "Млекопитающее", "птица": "Птица", "рептилия": "Рептилия", "земноводное": "Земноводное", "рыба": "Рыба", "насекомое": "Насекомое", "паукообразное": "Паукообразное", "моллюск": "Моллюск", "ракообразное": "Ракообразное", "червь": "Червь"}
FLORA_TYPE = {"дерево": "Дерево", "кустарник": "Кустарник", "трава": "Травянистое растение", "цветок": "Цветущее растение", "папоротник": "Папоротник", "мох": "Мох", "водоросль": "Водоросль"}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def normalize(text: str) -> str:
    return text.strip().lower().replace("ё", "е")

def extract_date(text: str) -> Optional[str]:
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b", text)
    if m:
        d, mo, y = m.groups()
        return f"{int(d):02d}.{int(mo):02d}.{y}"
    m = re.search(r"\b(19|20)\d{2}\b", text)
    if m: return m.group(0)
    return None

def extract_author(text: str) -> Optional[str]:
    m = re.search(r"(автор|фото|by)\s+([a-zа-яё0-9@._-]+)", text)
    if m: return m.group(2)
    return None

def match_from_synonyms(text: str, mapping: Dict[str, List[str]]) -> Optional[str]:
    normalized_text = normalize(text)
    for canon_value, synonyms in mapping.items():
        for s in synonyms:
            if s in normalized_text:
                return canon_value
    return None

# --- ОСНОВНАЯ ФУНКЦИЯ КЛАССИФИКАЦИИ ---
def classify_features(raw_features: List[str]) -> Dict[str, Any]:
    features: Dict[str, Any] = {}

    for phrase in raw_features:
        p_normalized = normalize(phrase)
        p_nominative = normalize_to_nominative(p_normalized)

        # 1. Проверяем сезон
        season = match_from_synonyms(p_normalized, SEASONS)
        if season: features["season"] = season

        # 2. Проверяем среду обитания (habitat) с помощью синонимов
        habitat = match_from_synonyms(p_normalized, HABITAT)
        if habitat: features["habitat"] = habitat
        
        # 3. Проверяем облачность с помощью синонимов
        cloud = match_from_synonyms(p_normalized, CLOUDINESS)
        if cloud: features["cloudiness"] = cloud
        
        # 4. Проверяем тип флоры
        for key, canon_value in FLORA_TYPE.items():
            if key in p_normalized:
                features["flora_type"] = canon_value
                break

        # 5. Проверяем тип фауны
        for key, canon_value in FAUNA_TYPE.items():
            if key in p_normalized:
                features["fauna_type"] = canon_value
                break

        # 6. Проверяем цветение
        if "цветет" in p_normalized or "цветение" in p_normalized:
            features["flowering"] = True

        # 7. Проверяем наличие/отсутствие и тип плодов
        if any(w in p_normalized for w in ["без плодов", "без ягод", "без шишек"]):
            features["fruits_present"] = "Нет"
        else:
            for fruit_key, canon_value in FRUITS.items():
                if fruit_key in p_nominative.split():
                    features["fruits_present"] = canon_value
                    break

        # 8. Извлекаем автора
        author = extract_author(p_normalized)
        if author: features["author"] = author

        # 9. Извлекаем дату
        date = extract_date(p_normalized)
        if date: features["date"] = date
            
    return features

# --- КОНЕЦ ФАЙЛА RasaProject/logic/classify_features.py ---