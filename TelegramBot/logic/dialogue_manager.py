# --- НАЧАЛО ФАЙЛА: logic/dialogue_manager.py ---

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
        if not self.context_manager.redis_client:
            return intent, entities

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}
        
        final_intent = intent
        final_entities = entities.copy()
        last_intent = last_item.get("intent")
        last_entities = last_item.get("entities", {})
        
        is_ambiguous_query = len(query.split()) <= 3 and not any(verb in query.lower() for verb in ACTION_VERBS)

        if is_ambiguous_query and last_intent:
            logger.info(f"Неявный запрос. Заменяем интент '{final_intent}' на интент из истории: '{last_intent}'")
            final_intent = last_intent
        elif final_intent == "unknown" and last_intent:
            final_intent = last_intent
            
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

    async def get_comparison_pair(
        self, user_id: str, current_intent: str, current_entities: Dict[str, Any], current_category: Optional[str]
    ) -> Optional[Dict[str, str]]:
        """
        Только читает историю и проверяет, можно ли предложить сравнение.
        """
        if not self.context_manager.redis_client:
            return None

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}

        current_object = current_entities.get("object")
        last_object = last_item.get("object")
        last_category = last_item.get("category")

        if (current_intent in ["get_text", "get_picture"] and
                current_object and current_category and
                last_object and last_category == current_category and
                last_object != current_object):
            return {"object1": last_object, "object2": current_object}
        
        return None

    async def update_history(
        self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str]
    ):
        """
        Только пишет в историю. Вызывается после успешного ответа.
        """
        if not self.context_manager.redis_client:
            return

        if final_entities.get("object") or final_entities.get("geo_place"):
            user_context = await self.context_manager.get_context(user_id)
            history = user_context.get("history", [])
            
            new_history_item = {
                "intent": final_intent,
                "entities": final_entities,
                "object": final_entities.get("object"),
                "category": object_category
            }
            updated_history = [new_history_item] + history[:1]
            
            user_context['history'] = updated_history
            await self.context_manager.set_context(user_id, user_context)
            logger.info(f"История для user_id={user_id} обновлена: {new_history_item}")

# --- КОНЕЦ ФАЙЛА: logic/dialogue_manager.py ---