import logging
from utils.context_manager import RedisContextManager
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class DialogueManager:
    def __init__(self, context_manager: RedisContextManager):
        """
        Инициализирует менеджер диалогов.
        :param context_manager: Экземпляр менеджера для работы с хранилищем (Redis).
        """
        self.context_manager = context_manager

    def process_turn(self, user_id: str, query: str, intent: str, current_entities: Dict[str, Any], object_category: Optional[str]) -> tuple[str, Dict[str, Any], Optional[Dict[str, str]]]:
        """
        ЕДИНЫЙ МЕТОД для обработки шага диалога.
        1. Обогащает запрос с помощью контекста.
        2. Проверяет возможность сравнения.
        3. Обновляет контекст в Redis.
        :return: Кортеж (final_intent, final_entities, comparison_pair | None).
        """
        if not self.context_manager.redis_client:
            return intent, current_entities, None

        # --- 1. Обогащение ---
        user_context = self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_item = history[0] if history else {}
        
        final_intent, final_entities = self._enrich_request(user_id, query, intent, current_entities, last_item)

        # --- 2. Проверка на сравнение ---
        comparison_pair = self._check_for_comparison(user_id, final_intent, final_entities, object_category, last_item)

        # --- 3. Обновление контекста ---
        self._update_history(user_id, final_intent, final_entities, object_category, history)

        return final_intent, final_entities, comparison_pair

    def _enrich_request(self, user_id: str, query: str, intent: str, current_entities: Dict[str, Any], last_item: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Применяет эвристики для обогащения запроса."""
        final_intent = intent
        final_entities = current_entities.copy()
        last_intent = last_item.get("intent")
        last_entities = last_item.get("entities", {})

        is_simple_object_follow_up = (final_entities.get("object") and not final_entities.get("features") and not final_entities.get("geo_place") and len(query.split()) <= 3)

        if is_simple_object_follow_up and last_intent and last_entities.get("object") and final_entities.get("object") != last_entities.get("object"):
            logger.info(f"Контекст [ID: {user_id}]: Короткий запрос с НОВЫМ объектом. Принудительно используем старый интент: '{last_intent}'")
            final_intent = last_intent
        elif final_intent == "unknown" and last_intent:
            logger.info(f"Контекст [ID: {user_id}]: Неясный интент. Используем старый интент из контекста: '{last_intent}'")
            final_intent = last_intent
            
        if not final_entities.get("object") and last_entities.get("object"):
            logger.info(f"Контекст [ID: {user_id}]: В запросе нет объекта. Используем сохраненный: '{last_entities.get('object')}'")
            final_entities["object"] = last_entities.get("object")
        
        if final_entities.get("object") and final_entities.get("object") == last_entities.get("object"):
            merged_features = last_entities.get("features", {}).copy()
            merged_features.update(final_entities.get("features", {}))
            final_entities["features"] = merged_features

        return final_intent, final_entities

    def _check_for_comparison(self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str], last_item: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """Проверяет, можно ли предложить сравнение."""
        current_object_name = final_entities.get("object")
        last_object_name = last_item.get("object")
        last_object_category = last_item.get("category")

        if (final_intent == "get_text" and current_object_name and object_category and last_object_name and last_object_category == object_category and last_object_name != current_object_name):
            logger.info(f"Контекст [ID: {user_id}]: Найдена пара для сравнения! ('{current_object_name}' vs '{last_object_name}')")
            return {"object1": last_object_name, "object2": current_object_name}
        return None

    def _update_history(self, user_id: str, final_intent: str, final_entities: Dict[str, Any], object_category: Optional[str], old_history: list):
        """Сохраняет новый, актуальный контекст в Redis."""
        if final_entities.get("object") or final_entities.get("geo_place"):
            new_history_item = {
                "intent": final_intent,
                "entities": final_entities,
                "object": final_entities.get("object"),
                "category": object_category
            }
            updated_history = [new_history_item] + old_history[:1]
            self.context_manager.set_context(user_id, {"history": updated_history})
        else:
            self.context_manager.delete_context(user_id)