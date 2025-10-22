import re
from typing import Optional

# Объекты, которые обычно находятся НА берегу (рядом с Байкалом)
SHORE_OBJECTS = {
    'заповедник', 'заповедники', 'заповедные', 'национальный парк', 'парк', 
    'музей', 'музеи', 'база отдыха', 'базы отдыха', 'отель', 'отели',
    'гостиница', 'гостиницы', 'кафе', 'ресторан', 'рестораны',
    'турбаза', 'кемпинг', 'поселок', 'деревня', 'город', 'поселение',
    'деревня', 'село', 'архитектур', 'памятник', 'монастырь', 'церковь',
    'собор', 'храм', 'часовня'
}

# Объекты, которые обычно находятся В Байкале
WATER_OBJECTS = {
    'остров', 'острова', 'бухта', 'бухты', 'залив', 'заливы',
    'мыс', 'рыба', 'рыбалка', 'купание', 'ныряние', 'дайвинг',
    'паром', 'лодка', 'катер', 'судно', 'корабль', 'яхта',
    'пляж', 'берег', 'волна', 'течение'
}

def determine_baikal_relation(query: str, entity_name: str = "", entity_type: str = "") -> Optional[str]:
    """
    Определяет отношение запроса к Байкалу на основе анализа текста и контекста объекта.
    
    Args:
        query: Оригинальный запрос пользователя
        entity_name: Название основного объекта
        entity_type: Тип основного объекта
    
    Returns:
        "на Байкале", "рядом с Байкалом" или None если Байкал не упоминается
    """
    query_lower = query.lower()
    entity_name_lower = entity_name.lower()
    
    # Проверяем упоминание Байкала с помощью регулярки (учитываем опечатки)
    baikal_pattern = re.compile(r'байкал?[а-я]*')
    has_baikal_mention = bool(baikal_pattern.search(query_lower))
    
    if not has_baikal_mention:
        return None
    
    # Регулярки для определения контекста
    near_patterns = [
        r'рядом\s+с\s+байкал', r'около\s+байкал', r'у\s+байкал', r'близ\s+байкал',
        r'прибайкал', r'окрестност', r'поблизости', r'вокруг\s+байкал'
    ]
    
    on_patterns = [
        r'на\s+байкал', r'в\s+байкал', r'озер', r'воды', r'акватор', r'глубин'
    ]
    
    # Проверяем паттерны "рядом"
    has_near = any(re.search(pattern, query_lower) for pattern in near_patterns)
    # Проверяем паттерны "на"
    has_on = any(re.search(pattern, query_lower) for pattern in on_patterns)
    
    # Анализируем контекст объекта
    is_shore_object = any(obj in entity_name_lower or obj in query_lower for obj in SHORE_OBJECTS)
    is_water_object = any(obj in entity_name_lower or obj in query_lower for obj in WATER_OBJECTS)
    
    # Специальная логика для заповедных мест - они всегда "рядом с Байкалом"
    if any(word in query_lower for word in ['заповедные', 'заповедник', 'заказник']):
        return "рядом/около Байкала"
    
    # Логика определения отношения к Байкалу
    if has_near:
        return "рядом/около Байкала"
    elif has_on:
        return "в/на Байкале"
    elif is_water_object:
        return "в/на Байкале"
    elif is_shore_object:
        # Для объектов на берегу по умолчанию "рядом с Байкалом"
        return "рядом/около Байкала"
    else:
        # Если не удалось определить по контексту, используем "рядом с Байкалом" как более безопасный вариант
        return "рядом/около Байкала"