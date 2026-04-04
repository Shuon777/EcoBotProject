import logging
import json
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from ..llm_factory import LLMFactory

logger = logging.getLogger("BiologyWorker")

# 1. Схема ТОЛЬКО для LLM (Минимальная)
class LLMBiologyExtraction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Optional[Literal["describe", "show_image", "show_map", "list_items", "find_nearby"]] = None
    species_name: Optional[str] = Field(None, description="Название вида в именительном падеже (например, лиственница сибирская)")
    category: Optional[Literal["Flora", "Fauna", "Unknown"]] = None
    location_context: Optional[str] = Field(None, description="Только если явно указан город/место. Иначе null.")

# 2. Полная схема (которую ожидает Orchestrator)
class BiologyAnalysis(BaseModel):
    action: Optional[str] = None
    species_name: Optional[str] = None
    category: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)
    location_context: Optional[str] = None

class BiologyWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        # LLM видит только урезанную схему!
        self.parser = llm.with_structured_output(LLMBiologyExtraction)
    
    def _detect_action_by_triggers(self, query: str) -> Optional[str]:
        query_lower = query.lower()
        if any(w in query_lower for w in["карт", "где обитает", "где растет", "ареал"]): return "show_map"
        if any(w in query_lower for w in["рядом", "около", "возле", "в районе", "поблизости"]): return "find_nearby"
        if any(w in query_lower for w in["фото", "выглядит", "покажи", "картинк"]): return "show_image"
        if any(w in query_lower for w in["список", "какие", "перечисли", "какая флора", "какая фауна"]): return "list_items"
        if any(w in query_lower for w in ["расскажи", "что такое", "описание", "информаци"]): return "describe"
        return None

    def _detect_attributes_by_triggers(self, query: str) -> Dict[str, str]:
        """Атрибуты извлекаются ТОЛЬКО скриптом. LLM об этом даже не знает."""
        query_lower = query.lower()
        attrs = {}

        if any(w in query_lower for w in ["зим"]): attrs["season"] = "Зима"
        elif any(w in query_lower for w in ["весн"]): attrs["season"] = "Весна"
        elif any(w in query_lower for w in ["летом", "летн"]): attrs["season"] = "Лето"
        elif any(w in query_lower for w in ["осен"]): attrs["season"] = "Осень"

        if any(w in query_lower for w in["цветущ", "цветет", "расцвел"]): attrs["flowering"] = "Да"
        
        if any(w in query_lower for w in ["шишк"]): attrs["fruits_present"] = "Шишка"
        elif any(w in query_lower for w in ["ягод"]): attrs["fruits_present"] = "Ягода"
        elif any(w in query_lower for w in ["плод"]): attrs["fruits_present"] = "Плод"

        if any(w in query_lower for w in ["болот"]): attrs["habitat"] = "Болото"
        elif any(w in query_lower for w in["берег", "побереж"]): attrs["habitat"] = "Побережье"
        elif any(w in query_lower for w in ["степ"]): attrs["habitat"] = "Степь"
        elif any(w in query_lower for w in ["гор", "скал"]): attrs["habitat"] = "Горы"
        elif any(w in query_lower for w in ["лес"]): attrs["habitat"] = "Лес"

        return attrs

    async def analyze(self, query: str) -> tuple[BiologyAnalysis, str]:
        # 1. 100% надежная работа скриптов
        fast_action = self._detect_action_by_triggers(query)
        python_attributes = self._detect_attributes_by_triggers(query)
        
        # 2. Если Python нашел Action, говорим LLM не думать об этом
        action_hint = ""
        if fast_action:
            action_hint = f"СИСТЕМНАЯ НАВОДКА: action = '{fast_action}'. Твоя задача только извлечь species_name."

        prompt = f"""
        ЗАДАЧА: ИЗВЛЕЧЬ ДАННЫЕ ИЗ ЗАПРОСА ПОЛЬЗОВАТЕЛЯ В JSON.
        ЯЗЫК: ТОЛЬКО РУССКИЙ.

        {action_hint}

        ВАЖНЫЕ ПРАВИЛА:
        1. Общие слова в запросе: "флора", "фауна", "растения", "животные" могут указываться в поле species_name, если являются объектом запроса.

        СТРОГИЕ ПРАВИЛА ИЗВЛЕЧЕНИЯ:
        1. ПОЛЕ species_name: Извлечь биологический объект запроса. Сохранить порядок слов. Поставить в иминительный падеж.
        2. ПОЛЕ action:
           - describe: рассказать, описать.
           - show_image: показать фото, как выглядит.
           - show_map: показать на карте, где обитает/растет.
           - list_items: списки.
           - find_nearby: найти рядом.
        3. ПОЛЕ category: Тип поля species_name: Flora(растения/грибы), Fauna (животные/рыбы).
        4. ПОЛЕ 'location_context': Если локация отсутствует в тексте — null. Запрещено выдумывать.
        
        ЗАПРОС: {query}
        """
        logger.info(f"🧬 Analyzing biology request: '{query}'")
        
        try:
            # 3. LLM извлекает только сложную семантику (падежи)
            llm_result: LLMBiologyExtraction = await self.parser.ainvoke(prompt)
            
            # 4. СБОРКА ИТОГОВОГО ОБЪЕКТА (Python + LLM)
            final_action = fast_action if fast_action else (llm_result.action or "describe")
            
            final_result = BiologyAnalysis(
                action=final_action,
                species_name=llm_result.species_name,
                category=llm_result.category,
                attributes=python_attributes, # Атрибуты вставляем в обход LLM!
                location_context=llm_result.location_context
            )

            source = "FAST_ACTION + LLM_ENTITY" if fast_action else "FULL_LLM"
            debug_trace = f"🧬 Biology NLU <i>({source})</i>:\n{json.dumps(final_result.model_dump(), indent=2, ensure_ascii=False)}"
            
            return final_result, debug_trace
            
        except Exception as e:
            logger.error(f"BiologyWorker error: {e}")
            # Безопасный фоллбэк со всеми данными, которые успел собрать Python
            fallback = BiologyAnalysis(action=fast_action or "describe", attributes=python_attributes)
            return fallback, f"🧬 ERROR in LLM, fallback to python rules"