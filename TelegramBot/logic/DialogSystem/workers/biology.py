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

    action: Literal["describe", "show_image", "show_map", "list_items", "find_nearby"]
    species_name: str = Field(description="Название вида в именительном падеже (нерпа, кедр)")
    category: Literal["Flora", "Fauna", "Unknown"]
    attributes: Dict[str, str] = Field(
        default_factory=dict, 
        description="Атрибуты: season (Зима/Весна/Лето/Осень), habitat (болото, берег), fruits_present, flowering"
    )
    location_context: Optional[str] = Field(None, description="Только если в запросе ЯВНО указан город/место. Иначе null.")

class BiologyWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(BiologyAnalysis)

    async def analyze(self, query: str) -> BiologyAnalysis:
        has_near = any(m in query.lower() for m in ["около", "рядом", "возле", "в районе"])
        hint = "СИСТЕМНАЯ НАВОДКА: Замечен маркер близости. Приоритет action: find_nearby." if has_near else ""
        prompt = f"""
        ЗАДАЧА: Извлечь данные в JSON.
        ЛОКАЛЬ: Только кириллица.
        {hint}
        
        СТРОГИЕ ПРАВИЛА:
        1. Поле 'species_name': Поставь в именительный падеж. Запрещена латиница и смешанные символы типа 'ęd'.
        2. action: 
           - describe: если просят рассказать, описать.
           - show_image: если просят показать фото, как выглядит.
           - show_map: если просят показать на карте, где обитает.
           - list_items: если просят список (какая флора на Ольхоне).
           - find_nearby: если просят найти объект РЯДОМ с другим местом ("около", "возле", "в районе")
        3. category: Flora (растения/грибы), Fauna (животные/рыбы).
        4. Поле 'attributes': Только присутствующие в запросе прилагательные (например, "зимний", "лесной"). Запрещена генерация "описания" или фактов об объекте.
        5. Поле 'location_context': Если локация отсутствует в запросе — null. Запрещено генерировать локации.
        6. ЗАПРЕЩЕНО: Добавлять любые ключи, отсутствующие в схеме.
        
        Запрос: {query}
        """
        logger.info(f"🧬 Analyzing biology request: '{query}'")
        result = await self.parser.ainvoke(prompt)
        debug_trace = f"🧬 Biology NLU Trace:\n{json.dumps(result.model_dump(), indent=2, ensure_ascii=False)}"
        return result, debug_trace