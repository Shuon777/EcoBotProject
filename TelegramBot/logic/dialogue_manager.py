# --- НАЧАЛО ФАЙЛА: logic/dialogue_manager.py ---

import logging
from typing import Dict, Any, Optional

from utils.context_manager import RedisContextManager

logger = logging.getLogger(__name__)

# Определяем, какие сущности обязательны для каждого намерения.
REQUIRED_ENTITIES = {
    "get_picture": ["object"],
    "get_text": ["object"],
    "get_location": ["object"],
    "get_objects_in_polygon": ["geo_place"],
    "get_intersection_object_on_map": ["object", "geo_place"],
    "get_comparison": ["object1", "object2"]
}

class DialogueManager:
    def __init__(self, context_manager: RedisContextManager):
        """
        Инициализирует менеджер диалогов.
        :param context_manager: Асинхронный экземпляр менеджера для работы с Redis.
        """
        self.context_manager = context_manager

    async def process_turn(self, user_id: str, query: str, intent: str, current_entities: Dict[str, Any], object_category: Optional[str]) -> tuple[str, Dict[str, Any], Optional[Dict[str, str]]]:
        """
        ЕДИНЫЙ АСИНХРОННЫЙ МЕТОД для обработки шага диалога.
        """
        if not self.context_manager.redis_client:
            return intent, current_entities, None

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}
        
        final_intent, final_entities = self._enrich_request(user_id, query, intent, current_entities, last_item)
        comparison_pair = self._check_for_comparison(user_id, final_intent, final_entities, object_category, last_item)
        await self._update_history(user_id, final_intent, final_entities, object_category, history)

        return final_intent, final_entities, comparison_pair

    def _enrich_request(self, user_id: str, query: str, intent: str, current_entities: Dict[str, Any], last_item: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """(Синхронная функция) Применяет эвристики для обогащения запроса."""
        final_intent = intent
        final_entities = current_entities.copy()
        last_intent = last_item.get("intent")
        last_entities = last_item.get("entities", {})
        
        # --- Логика эвристик ---
        ACTION_VERBS = ["расскажи", "покажи", "опиши", "выглядит", "где", "найти", "растет", "обитает"]
        has_action_verb = any(verb in query.lower() for verb in ACTION_VERBS)
        is_simple_follow_up = len(query.split()) <= 3 and not has_action_verb

        # 1. Коррекция интента
        if is_simple_follow_up and last_intent:
            final_intent = last_intent
        elif final_intent == "unknown" and last_intent:
            final_intent = last_intent
            
        # 2. Обогащение объекта
        if not final_entities.get("object") and last_entities.get("object"):
            final_entities["object"] = last_entities.get("object")
        
        # [# ФИНАЛЬНОЕ ИСПРАВЛЕНИЕ] 3. Объединение признаков
        # Эта логика теперь проста и надежна.
        # Если финальный объект (после обогащения) тот же, что и в прошлом шаге,
        # то мы берем признаки из прошлого шага и добавляем/обновляем их новыми.
        if final_entities.get("object") and final_entities.get("object") == last_entities.get("object"):
            # Берем признаки из прошлого полного запроса
            base_features = last_entities.get("features", {}).copy()
            # Берем признаки из текущего короткого запроса ("А осенью")
            new_features = current_entities.get("features", {})
            # Объединяем их
            base_features.update(new_features)
            # Присваиваем результат
            final_entities["features"] = base_features

        return final_intent, final_entities

    def _check_for_comparison(self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str], last_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """(Синхронная функция) Проверяет, можно ли предложить сравнение."""
        current_object_name = final_entities.get("object")
        last_object_name = last_item.get("object")
        last_object_category = last_item.get("category")

        if (final_intent == "get_text" and current_object_name and object_category and last_object_name and last_object_category == object_category and last_object_name != current_object_name):
            return {"object1": last_object_name, "object2": current_object_name}
        return None

    async def _update_history(self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str], old_history: list):
        """(Асинхронная функция) Сохраняет новый, актуальный контекст в Redis."""
        if final_entities.get("object") or final_entities.get("geo_place"):
            new_history_item = {
                "intent": final_intent,
                "entities": final_entities,
                "object": final_entities.get("object"),
                "category": object_category
            }
            updated_history = [new_history_item] + old_history[:1]
            await self.context_manager.set_context(user_id, {"history": updated_history})
        else:
            await self.context_manager.delete_context(user_id)

# --- КОНЕЦ ФАЙЛА: logic/dialogue_manager.py ---