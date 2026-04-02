# TelegramBot/logic/dialogue_system/workers/biology.py
import logging
import json
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field,ConfigDict
from ..llm_factory import LLMFactory

logger = logging.getLogger("BiologyWorker")

# Схема ТОЛЬКО для биологии
class BiologyAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Optional[Literal["describe", "show_image", "show_map", "list_items", "find_nearby"]] = None
    species_name: str = Field(None, description="Название вида в именительном падеже (нерпа, лиственница сибирская)")
    category: Optional[Literal["Flora", "Fauna", "Unknown"]] = None
    # НОВОЕ ОПИСАНИЕ ПОЛЯ:
    attributes: Dict[str, str] = Field(
        default_factory=dict,
        description="ТОЛЬКО ЕСЛИ ЯВНО УКАЗАНО В ТЕКСТЕ. Если признаков в запросе нет, должен быть пустой словарь {}. Разрешенные ключи: season, habitat, fruits_present, flowering."
    )
    location_context: Optional[str] = Field(None, description="Только если в запросе ЯВНО указан город/место. Иначе null.")

class BiologyWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(BiologyAnalysis)

    async def analyze(self, query: str) -> BiologyAnalysis:
        has_near = any(m in query.lower() for m in["около", "рядом", "возле", "в районе"])
        hint = "СИСТЕМНАЯ НАВОДКА: Замечен маркер близости. Приоритет action: find_nearby." if has_near else ""
        
        prompt = f"""
        ЗАДАЧА: Извлечь данные из текста пользователя в JSON.
        ЛОКАЛЬ: Только кириллица.
        {hint}
        СТРОГИЕ ПРАВИЛА:
        1. Поле 'species_name': Сохраняй порядок слов как в запросе (например, "лиственница сибирская"). Именительный падеж.
        2. action:
           - describe: рассказать, описать.
           - show_image: показать фото, как выглядит, "а осенью?" (подразумевает фото).
           - show_map: показать на карте, где обитает/растет.
           - list_items: списки.
           - find_nearby: найти рядом.
        3. category: Flora (растения/грибы), Fauna (животные/рыбы).
        4. Поле 'attributes'. ИЗВЛЕКАЙ ТОЛЬКО ТО, ЧТО ЯВНО НАПИСАНО В ЗАПРОСЕ ПОЛЬЗОВАТЕЛЯ!
           КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать свойства объекта из своих знаний ботаники!
           Если в тексте нет указания сезона, плодов или места обитания — оставляй словарь attributes ПУСТЫМ ({{}}).
           Разрешенные ключи (только если они ЕСТЬ в тексте):
           - "season": "Зима", "Весна", "Лето", "Осень". (Или производные "осенью", "зимой" и т.п.)
           - "habitat": среда обитания ("болото", "побережье", "степь").
           - "fruits_present": плоды ("шишка", "ягода").
           - "flowering": "Да" (если есть "цветет", "цветущий").
        5. Поле 'location_context': Если локация отсутствует в тексте — null. Запрещено выдумывать.
        
        Запрос: {query}
        """
        logger.info(f"🧬 Analyzing biology request: '{query}'")
        result = await self.parser.ainvoke(prompt)
        debug_trace = f"🧬 Biology NLU Trace:\n{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}"
        return result, debug_trace