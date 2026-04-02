# TelegramBot/logic/dialogue_system/workers/infrastructure.py
import logging
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from ..llm_factory import LLMFactory

logger = logging.getLogger("InfrastructureWorker")

class InfraAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid')

    action: Optional[Literal["describe", "show_map", "list_items", "count_items"]] = None
    object_name: Optional[str] = Field(None)
    entity_type: Literal["Infrastructure", "GeoPlace", "Service", "Unknown"] = "Unknown"
    category: Optional[Literal["Природный объект", "Достопримечательности", "Unknown"]] = "Unknown"
    
    subcategory: List[str] = Field(default_factory=list)
    area_name: Optional[str] = Field(None)

class InfrastructureWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(InfraAnalysis)

async def analyze(self, query: str) -> InfraAnalysis:
        prompt = f"""
        ЗАДАЧА: Извлечь данные из текста пользователя в JSON.
        ЛОКАЛЬ: Только кириллица.
        
        СТРОГИЕ ПРАВИЛА:
        1. Поле 'object_name': ЧТО именно ищем? (переведи в именительный падеж, например: "памятник", "музей", "скала").
        2. Поле 'area_name': ГДЕ ищем? (населенный пункт, остров, регион, например: "Иркутск", "Ольхон"). Если места нет — null.
        3. Поле 'action':
           - describe: если просят рассказать об одном конкретном объекте, история.
           - list_items: если просят перечислить объекты, найти "какие есть", "список".
           - show_map: если просят показать на карте, "где находится".
           - count_items: если спрашивают "сколько".
        4. Поле 'entity_type': Всегда ставь "Infrastructure" для объектов (памятников, гор, музеев). 
        5. Поле 'category'. СТРОГО ОДНО ИЗ ДВУХ:
           - "Достопримечательности" — для РУКОТВОРНЫХ объектов (памятники, архитектура, музеи, наука, базы отдыха).
           - "Природный объект" — для объектов, созданных ПРИРОДОЙ (горы, скалы, мысы, пещеры, минеральные источники).
        6. Поле 'subcategory': массив строк, уточняющий тип (например: ["Памятники"], ["Музеи"], ["Скалы"], ["Горы"]).
        
        Запрос: {query}
        """
        logger.info(f"🏛️ Analyzing infrastructure request: '{query}'")
        return await self.parser.ainvoke(prompt)