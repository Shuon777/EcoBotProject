import logging
import json
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict
from ..llm_factory import LLMFactory

logger = logging.getLogger("InfrastructureWorker")

# 1. Схема ТОЛЬКО для LLM
class LLMInfraExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Optional[Literal["describe", "show_map", "list_items", "count_items"]] = None
    object_name: Optional[str] = Field(None)
    entity_type: Literal["Infrastructure", "GeoPlace", "Service", "Unknown"] = "Unknown"
    category: Optional[Literal["Природный объект", "Достопримечательности", "Unknown"]] = "Unknown"
    subcategory: List[str] = Field(default_factory=list)
    area_name: Optional[str] = Field(None)

# 2. Полная схема (которую ожидает Orchestrator)
class InfraAnalysis(BaseModel):
    action: Optional[str] = None
    object_name: Optional[str] = None
    entity_type: str = "Unknown"
    category: str = "Unknown"
    subcategory: List[str] = Field(default_factory=list)
    area_name: Optional[str] = None

class InfrastructureWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(LLMInfraExtraction)

    def _detect_action_by_triggers(self, query: str) -> Optional[str]:
        """FAST PATH: Находим действие по корням слов."""
        query_lower = query.lower()
        
        # 1. count_items (Самый сильный маркер)
        if any(w in query_lower for w in ["скольк"]):
            return "count_items"
            
        # 2. list_items (Запрос списков)
        if any(w in query_lower for w in["какие", "список", "перечисли", "что интересного", "все "]):
            return "list_items"
            
        # 3. show_map (Запрос местоположения)
        if any(w in query_lower for w in["карт", "где находит", "где располож", "покажи где"]):
            return "show_map"
            
        # 4. describe (Запрос информации)
        if any(w in query_lower for w in["расскажи", "история", "что такое", "описание", "про "]):
            return "describe"

        return None

    async def analyze(self, query: str) -> tuple[InfraAnalysis, str]:
        # 1. Пытаемся определить Action быстрым путем
        fast_action = self._detect_action_by_triggers(query)
        
        # 2. Подсказка для LLM
        action_hint = ""
        if fast_action:
            action_hint = f"\nСИСТЕМНАЯ НАВОДКА: action = '{fast_action}'. Твоя главная задача — извлечь object_name, area_name и классифицировать их.\n"

        prompt = f"""
        ЗАДАЧА: ИЗВЛЕЧЬ ДАННЫЕ ИЗ ЗАПРОСА ПОЛЬЗОВАТЕЛЯ В JSON.
        ЯЗЫК: ТОЛЬКО РУССКИЙ.
        {action_hint}
        
        СТРОГИЕ ПРАВИЛА:
        1. ПОЛЕ 'object_name': ЧТО именно ищем? (переведи в именительный падеж, например: "памятник", "Байкальский музей", "скала").
        2. ПОЛЕ 'area_name': ГДЕ ищем? (населенный пункт, остров, регион, например: "Иркутск", "Ольхон"). Если места нет — null.
        3. ПОЛЕ 'entity_type': Всегда ставь "Infrastructure" для объектов (памятников, гор, музеев). 
        4. ПОЛЕ 'category'. СТРОГО ОДНО ИЗ ДВУХ:
           - "Достопримечательности" — Общий термин для РУКОТВОРНЫХ объектов (памятники, архитектура, музеи, наука, базы отдыха).
           - "Природный объект" — для объектов, созданных ПРИРОДОЙ И ЧАСТИЧНО ЧЕЛОВЕКОМ (горы, скалы, мысы, пещеры, минеральные источники, заповедники, заказники).
        5. ПОЛЕ 'subcategory': массив строк, уточняющий тип (например: ["Памятники"], ["Музеи"],["Скалы"], ["Горы"]).
        
        ЗАПРОС: {query}
        """
        logger.info(f"🏛️ Analyzing infrastructure request: '{query}'")
        
        try:
            # 3. LLM парсит сложные сущности (названия и их типы)
            llm_result: LLMInfraExtraction = await self.parser.ainvoke(prompt)
            
            # 4. СБОРКА ИТОГОВОГО ОБЪЕКТА (Hard Override для Action)
            final_action = fast_action if fast_action else (llm_result.action or "describe")
            
            final_result = InfraAnalysis(
                action=final_action,
                object_name=llm_result.object_name,
                entity_type=llm_result.entity_type,
                category=llm_result.category,
                subcategory=llm_result.subcategory,
                area_name=llm_result.area_name
            )

            source = "FAST_ACTION + LLM_ENTITY" if fast_action else "FULL_LLM"
            debug_trace = f"🏛️ Infra NLU <i>({source})</i>:\n{json.dumps(final_result.model_dump(), indent=2, ensure_ascii=False)}"
            
            return final_result, debug_trace

        except Exception as e:
            logger.error(f"InfrastructureWorker error: {e}")
            fallback = InfraAnalysis(action=fast_action or "describe")
            return fallback, f"🏛️ ERROR in LLM, fallback to python rules"