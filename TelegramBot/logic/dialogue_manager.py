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
        
        # 🔴 ДОБАВИМ ЛОГИРОВАНИЕ ИСТОРИИ
        logger.info(f"История пользователя {user_id}: {history}")
        logger.info(f"Последний элемент истории: {last_item}")
        
        final_intent = intent
        final_entities = entities.copy()
        last_intent = last_item.get("intent")
        last_entities = last_item.get("entities", {})
        
        # 🔴 ДОБАВИМ ЛОГИРОВАНИЕ ПОСЛЕДНИХ СУЩНОСТЕЙ
        logger.info(f"Последние сущности из истории: {last_entities}")
        
        is_ambiguous_query = len(query.split()) <= 3 and not any(verb in query.lower() for verb in ACTION_VERBS)

        # 🔄 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Сначала проверяем ГЕОГРАФИЧЕСКИЕ КЛЮЧЕВЫЕ СЛОВА
        geo_keywords = ["заказник", "музей", "памятник", "заповедник", "научн", "учрежден", "достопримечательность"]
        query_lower = query.lower()
        
        has_geo_keyword = any(keyword in query_lower for keyword in geo_keywords)
        last_was_geo = last_intent in ["get_geo_objects", "get_geo_info", "get_geo_count"]
        
        logger.info(f"has_geo_keyword: {has_geo_keyword}, last_was_geo: {last_was_geo}, last_intent: {last_intent}")
        
        # 🔄 ПРИОРИТЕТ 1: Если есть географическое ключевое слово И (история географическая ИЛИ запрос неполный)
        if has_geo_keyword and (last_was_geo or is_ambiguous_query):
            if last_was_geo:
                # Берем географический intent из истории
                final_intent = last_intent
                logger.info(f"Географическое ключевое слово + история geo -> intent: {final_intent}")
            else:
                # Новый географический запрос
                final_intent = "get_geo_objects"
                logger.info(f"Географическое ключевое слово -> intent: {final_intent}")
            
            # 🔄 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Всегда обогащаем из истории если была geo история
            if last_was_geo and last_entities:
                logger.info(f"Обогащаем из истории: {last_entities}")
                
                # Берем ВСЕ географические сущности из истории
                if last_entities.get("location_info"):
                    final_entities["location_info"] = last_entities["location_info"].copy()
                    logger.info(f"Скопировали location_info: {final_entities['location_info']}")
                else:
                    logger.warning("В истории нет location_info!")
                    
                if last_entities.get("geo_type"):
                    final_entities["geo_type"] = last_entities["geo_type"].copy()
                    logger.info(f"Скопировали geo_type: {final_entities['geo_type']}")
                else:
                    logger.warning("В истории нет geo_type!")
            
            # Преобразуем старые сущности в новые географические (если нужно)
            if "object" in final_entities and not final_entities.get("geo_type"):
                # Конвертируем из старой структуры в новую
                object_name = final_entities.pop("object")
                final_entities.setdefault("location_info", {"exact_location": None, "region": None, "nearby_places": []})
                final_entities.setdefault("geo_type", {"primary_type": ["Достопримечательности"], "specific_types": []})
            
            # Определяем specific_types по ключевым словам
            if "заказник" in query_lower:
                final_entities["geo_type"]["specific_types"] = ["Заказники"]
            elif "музей" in query_lower:
                final_entities["geo_type"]["specific_types"] = ["Музеи"]
            elif "памятник" in query_lower:
                final_entities["geo_type"]["specific_types"] = ["Памятники"]
            elif "заповедник" in query_lower:
                final_entities["geo_type"]["specific_types"] = ["Заповедники"]
            
            # 🔄 ДОПОЛНИТЕЛЬНОЕ ОБОГАЩЕНИЕ: Если в истории была локация, используем ее
            if last_was_geo and last_entities.get("location_info"):
                if not final_entities.get("location_info"):
                    final_entities["location_info"] = {}
                
                # Обогащаем exact_location
                if not final_entities["location_info"].get("exact_location") and last_entities["location_info"].get("exact_location"):
                    final_entities["location_info"]["exact_location"] = last_entities["location_info"]["exact_location"]
                    logger.info(f"Обогатили exact_location: {final_entities['location_info']['exact_location']}")
                
                # Обогащаем region  
                if not final_entities["location_info"].get("region") and last_entities["location_info"].get("region"):
                    final_entities["location_info"]["region"] = last_entities["location_info"]["region"]
                    logger.info(f"Обогатили region: {final_entities['location_info']['region']}")
        
        # 🔄 ПРИОРИТЕТ 2: Старая логика для биологических запросов
        elif is_ambiguous_query and last_intent:
            logger.info(f"Неявный запрос. Заменяем интент '{final_intent}' на интент из истории: '{last_intent}'")
            final_intent = last_intent
            
        elif final_intent == "unknown" and last_intent:
            final_intent = last_intent
            
        # 🔄 ОБОГАЩЕНИЕ СУЩНОСТЕЙ (старая логика для биологических)
        if final_intent not in ["get_geo_objects", "get_geo_info", "get_geo_count"]:
            if not final_entities.get("object") and last_entities.get("object") and "object" in final_entities:
                final_entities["object"] = last_entities.get("object")

            if not final_entities.get("geo_place") and last_entities.get("geo_place") and "geo_place" in final_entities:
                final_entities["geo_place"] = last_entities.get("geo_place")
            
            if final_entities.get("object") and final_entities.get("object") == last_entities.get("object"):
                base_features = last_entities.get("features", {}).copy()
                new_features = entities.get("features", {})
                base_features.update(new_features)
                final_entities["features"] = base_features

        logger.info(f"Обогащенный запрос: intent={final_intent}, entities={final_entities}")
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
    self, user_id: str, final_intent: str, final_entities: Dict[str, Any], 
    object_category: Optional[str], original_query: str = None  # ← ДОБАВИТЬ
):
        if final_entities.get("object") or final_entities.get("geo_place"):
            user_context = await self.context_manager.get_context(user_id)
            history = user_context.get("history", [])
            
            new_history_item = {
                "intent": final_intent,
                "original_query": original_query,  # ← СОХРАНИТЬ
                "entities": final_entities,
                "object": final_entities.get("object"),
                "category": object_category
            }
            
            updated_history = [new_history_item] + history[:1]
            user_context['history'] = updated_history
            await self.context_manager.set_context(user_id, user_context)
            logger.info(f"История для user_id={user_id} обновлена: {new_history_item}")

# --- КОНЕЦ ФАЙЛА: logic/dialogue_manager.py ---