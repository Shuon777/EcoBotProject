import logging

logger = logging.getLogger(__name__)

GENERAL_CATEGORIES = {
    "достопримечательности",
    "научные учреждения", 
    "научные центры",
    "институты", 
    "музеи",
    "памятники",
    "памятник",
    "заповедники", 
    "заповедник",
    "заказники",
    "заказник",
    "заповедные места",
    "природоохранные территории"
}

PROTECTED_AREAS = "Природоохранные территории"

GROUP_ENTITY_MAP = {
    PROTECTED_AREAS: ["Заповедники", "Заказники"],
}

ENTITY_MAP = {
    "достопримечательности": "",
    "интересное место": "",
    "что интересного": "",

    # Синонимы для "Наука"
    "научные учреждения": "Наука",
    "научные центры": "Наука",
    "институты": "Наука",
    "наука": "Наука",
    "солнечная обсерватория": "Наука",

    # Синонимы для "Музеи"
    "музеи": "Музеи",
    "музей": "Музеи",
    "байкальский музей": "Музеи",
    "байкальский музей со ран": "Музеи",

    # Синонимы для "Памятники"
    "памятники": "Памятники",
    "памятник": "Памятники",

    # Синонимы для "Заповедники" (остаются для точных запросов)
    "заповедники": "Заповедники",
    "заповедник": "Заповедники",
    
    # Синонимы для "Заказники" (остаются для точных запросов)
    "заказники": "Заказники",
    "заказник": "Заказники",

    # Синонимы, которые теперь ссылаются на ГРУППУ
    "заповедные места": PROTECTED_AREAS,
    "природоохранные территории": PROTECTED_AREAS,
}

def should_include_object_name(entity_name: str) -> bool:
    """Нужно ли передавать object_name в запрос"""
    if not entity_name:
        return False
    return entity_name.lower() not in GENERAL_CATEGORIES

def normalize_entity_name(raw_name: str) -> str | None:
    """
    Приводит имя сущности, извлеченное LLM, к каноническому виду.
    """
    if not isinstance(raw_name, str):
        return None

    raw_name_lower = raw_name.lower()

    if raw_name_lower in ENTITY_MAP:
        normalized_name = ENTITY_MAP[raw_name_lower]
        if normalized_name:
             logger.info(f"Сущность '{raw_name}' нормализована в '{normalized_name}'")
        return normalized_name
    else:
        logger.warning(f"Для сущности '{raw_name}' не найдено правило нормализации. Используется исходное значение.")
        return raw_name.capitalize()