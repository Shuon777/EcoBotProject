# TelegramBot/logic/dialogue_system/router.py
import os
import logging
from typing import Literal, Optional, List
from pydantic import BaseModel, Field
from .llm_factory import LLMFactory

logger = logging.getLogger("ROUTER")

def _load_known_objects(file_name: str) -> List[str]:
    """Загружает список биообъектов из TXT-файла (по одному на строку)"""
    # 1. Получаем абсолютный путь к текущей директории (DialogSystem)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Склеиваем путь с именем файла
    file_path = os.path.join(current_dir, file_name)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        # Убираем лишние пробелы и пустые строки, приводим к нижнему регистру
        return [line.strip().lower() for line in f if line.strip()]

class Route(BaseModel):
    """Классификация намерения пользователя"""
    intent: Literal["BIOLOGY", "INFRASTRUCTURE", "KNOWLEDGE", "CHITCHAT"] = Field(
        description="BIOLOGY - флора/фауна, INFRASTRUCTURE - музеи/памятники, KNOWLEDGE - история/FAQ/почему/кто работает, CHITCHAT - болталка"
    )

class SemanticRouter:
    def __init__(self, provider: str = "qwen"):

        llm = LLMFactory.get_model(provider)
        self.runnable = llm.with_structured_output(Route)
        self.KNOWN_OBJECTS_LOWER = _load_known_objects('biological_entity.txt')
        logger.info(f"Загружен список биологических сущностей")
    
    # Убрали аргумент known_objects_lower, так как берем его из self
    async def _define_intent_by_triggers(self, query: str) -> str | None:
        # Для защиты от ложных срабатываний (например, "ель" внутри слова "апЕЛЬсин")
        # лучше добавить пробелы по краям, или использовать регулярки. Но для старта пойдет и так.
        query_lower = query.lower()
        
        # Приоритет 1
        known_triggers =["билет", "цен", "график", "экспонат", "выставк", "чучело", "картина", "экспозици", "оформить", "сколько стоит", "проект"]
        if any(trigger in query_lower for trigger in known_triggers):
            logger.info("FAST CLASS: Найден триггер для типа запроса KNOWLEDGE")
            return "KNOWLEDGE"
        # Приоритет 2
        if any(bio_obj in query_lower for bio_obj in self.KNOWN_OBJECTS_LOWER):
            logger.info("FAST CLASS: Найдена биологическая сущность - BIOLOGY")
            return "BIOLOGY"
        # Приоритет 3
        infra_triggers =["музей", "памятник", "база отдыха", "кафе", "туалет", "маршрут", "музе"]
        if any(trigger in query_lower for trigger in infra_triggers):
            logger.info("FAST CLASS: Найден триггер для типа запроса INFRASTRUCTURE")
            return "INFRASTRUCTURE"
        
        return None
    async def get_intent(self, query: str, last_intent: Optional[str] = None) -> tuple[str, str]:
        # 1. Пытаемся отработать без LLM (Fast Path)
        fast_intent = await self._define_intent_by_triggers(query)
        if fast_intent:
            return fast_intent, "FAST_RULES (Python)"

        # 2. Если скрипт не справился, вызываем LLM
        context_hint = f"\nКОНТЕКСТ: Предыдущая тема диалога: {last_intent}\n" if last_intent else ""
        prompt = f"""
        ЗАДАЧА: КЛАССИФИЦИРОВАТЬ ТИП ЗАПРОСА ИЗ ЗАДАННОГО СПИСКА.

        КЛЮЧЕВОЕ ПРАВИЛО: BIOLOGY и INFRASTRUCTURE определяются по объекту запроса, KNOWLEDGE определяется по смыслу запроса.
        
        КРИТЕРИИ:
        1. BIOLOGY: ЗАПРОС О растении или животном.
            ПРИМЕРЫ:
                "Как выглядит эдельвейс?" -> Прямой запрос о определенном растении (эдельвейс).
                "Покажи на карте где обитает полёвка около Байкальского музея" -> Объект запроса животное (полёвка).
                Ключевые слова: флора, фауна, растения, животные -> Также считаются BIOLOGY, если это объект запроса.
        2. INFRASTRUCTURE: ЗАПРОС О СОЗДАННЫХ ЧЕЛОВКОМ ОБЪЕКТАХ: зданиях, музеях, памятниках, заказниках, заповедниках, топонимах. 
            ПРИМЕРЫ: 
                "Сколько музеев?" -> Объект запроса музеи.
                "Какие научные учреждения есть около Байкала?" -> Объект запроса (научные учереждения) создан человеком.
                "Расскажи о Байкальском музее СО РАН" -> Объект запроса конкретный музей.
        3. KNOWLEDGE: ЗАПРОС О УСЛУГАХ: процедуры, правила, цены, билеты, история, экпозиция, выставка, экспонат.
            ПРИМЕРЫ:
                "Как купить билет в Байкальский музей СО РАН онлайн?" -> Смысл запроса услуга.
                "Какие экспонаты можно увидеть в экспозиции истории развития жизни?" -> Смысл запроса узнать о услуге.
                "Что представляет собой экспозиция истории развития жизни в процессе абиотических изменений в Байкальском музее СО РАН?" -> Смысл запроса узнать о услуге (экспозиции).
        4. CHAT: ЗАПРОС О small talk
            ПРИМЕРЫ: 
                "Привет"
                "Как дела?"
                "Спасибо"
        
        ЗАПРОС: "{query}"
        """
        try:
            result = await self.runnable.ainvoke(prompt)
            return result.intent, "LLM (Qwen)"
        except Exception as e:
            logger.error(f"Router error: {e}")
            return (last_intent or "CHITCHAT"), "ERROR_FALLBACK"