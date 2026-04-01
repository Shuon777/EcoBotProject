# TelegramBot/logic/dialogue_system/workers/infrastructure.py
import logging
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from ..llm_factory import LLMFactory

logger = logging.getLogger("InfrastructureWorker")

class InfraAnalysis(BaseModel):
    action: Literal["describe", "show_map", "list_items", "count_items"]
    object_name: str = Field(description="Название объекта или типа объектов (музеи, памятник, Байкальский музей)")
    entity_type: Literal["Infrastructure", "GeoPlace", "Service"] = "Infrastructure"
    category: Optional[str] = Field(None, description="Природный объект или Достопримечательности")
    subcategory: List[str] = Field(default_factory=list, description="Список: Музеи, Памятники, Наука, Скалы, Горы и т.д.")
    area_name: Optional[str] = Field(None, description="Географическое местоположение (Иркутск, Листвянка, Ольхон)")

class InfrastructureWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(InfraAnalysis)

    async def analyze(self, query: str) -> InfraAnalysis:
        prompt = f"""
        ЗАДАЧА: Извлечь данные в JSON.
        ЛОКАЛЬ: Только кириллица.

        СТРОГИЕ ПРАВИЛА:
        1. object_name: Что именно ищем? (приводи к именительному падежу).
        2. area_name: ГДЕ ищем? (населенный пункт, остров, регион).
        3. action:
           - describe: если просят рассказать об одном конкретном объекте.
           - list_items: если просят перечислить объекты или найти "какие есть".
           - show_map: если просят показать на карте.
           - count_items: если спрашивают "сколько".
        4. category/subcategory: Выбирай логически (Музеи, Памятники, Наука, Скалы, Мысы, Горы, Города).

        Запрос: {query}
        """
        logger.info(f"🏛️ Analyzing infrastructure request: '{query}'")
        return await self.parser.ainvoke(prompt)