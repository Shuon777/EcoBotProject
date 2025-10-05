import logging
from typing import Dict, Any, Optional, Tuple

from utils.context_manager import RedisContextManager

logger = logging.getLogger(__name__)

ACTION_VERBS = ["расскажи", "покажи", "опиши", "выглядит", "где", "найти", "растет", "обитает", "встретить"]

class DialogueManager:
    def __init__(self, context_manager: RedisContextManager):
        self.context_manager = context_manager

    
    async def enrich_request(
        self, user_id: str, query: str, intent: str, entities: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Обогащает неполный запрос, используя контекст из Redis.
        """
        if not self.context_manager.redis_client:
            return intent, entities

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}
        
        final_intent = intent
        final_entities = entities.copy()
        last_intent = last_item.get("intent")
        last_entities = last_item.get("entities", {})
        
        # [ИЗМЕНЕНО] Улучшенная логика обогащения интента
        is_ambiguous_query = len(query.split()) <= 3 and not any(verb in query.lower() for verb in ACTION_VERBS)

        # Если запрос неявный (например, "а эдельвейс?") И в истории есть интент,
        # то мы ПРИНУДИТЕЛЬНО используем интент из истории, игнорируя догадку LLM.
        if is_ambiguous_query and last_intent:
            logger.info(f"Неявный запрос. Заменяем интент '{final_intent}' на интент из истории: '{last_intent}'")
            final_intent = last_intent
        # В качестве запасного варианта, если LLM сам не уверен
        elif final_intent == "unknown" and last_intent:
            final_intent = last_intent
            
        # Обогащение сущностей остается прежним, оно работает корректно
        if not final_entities.get("object") and last_entities.get("object"):
            final_entities["object"] = last_entities.get("object")

        if not final_entities.get("geo_place") and last_entities.get("geo_place"):
            final_entities["geo_place"] = last_entities.get("geo_place")
        
        if final_entities.get("object") and final_entities.get("object") == last_entities.get("object"):
            base_features = last_entities.get("features", {}).copy()
            new_features = entities.get("features", {})
            base_features.update(new_features)
            final_entities["features"] = base_features

        return final_intent, final_entities

    async def update_and_check_comparison(
        self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str]
    ) -> Optional[Dict[str, str]]:
        """
        Обновляет историю диалога в Redis и проверяет, можно ли предложить сравнение.
        Эта функция вызывается для ВСЕХ осмысленных запросов.
        """
        if not self.context_manager.redis_client:
            return None
            
        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}

        comparison_pair = self._check_for_comparison(final_intent, final_entities, object_category, last_item)

        # Обновляем историю, только если запрос содержит ключевые сущности
        if final_entities.get("object") or final_entities.get("geo_place"):
            new_history_item = {
                "intent": final_intent,
                "entities": final_entities,
                "object": final_entities.get("object"),
                "category": object_category
            }
            updated_history = [new_history_item] + history[:1]
            await self.context_manager.set_context(user_id, {"history": updated_history})
        
        return comparison_pair

    def _check_for_comparison(
        self, final_intent: str, final_entities: Dict[str, Any],
        object_category: Optional[str], last_item: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """(Синхронная функция) Проверяет, можно ли предложить сравнение."""
        current_object_name = final_entities.get("object")
        last_object_name = last_item.get("object")
        last_object_category = last_item.get("category")

        if (final_intent == "get_text" and
                current_object_name and object_category and
                last_object_name and last_object_category == object_category and
                last_object_name != current_object_name):
            return {"object1": last_object_name, "object2": current_object_name}
        return None

# --- КОНЕЦ ФАЙЛА: logic/dialogue_manager.py ---