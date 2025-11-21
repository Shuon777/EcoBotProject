
import logging
from typing import Dict, Any, Optional
from copy import deepcopy

from utils.context_manager import RedisContextManager

logger = logging.getLogger(__name__)

class DialogueManager:
    def __init__(self, context_manager: RedisContextManager):
        self.context_manager = context_manager

    def _filter_blocked_responses(self, response: list) -> list:
        """Фильтрует нежелательные ответы бота перед сохранением в историю"""
        blocked_phrases = [
            "я не готов разговаривать",
            "я не готов про это разговаривать", 
            "я не могу разговаривать",
            "я не умею разговаривать",
            "извините, я не понимаю"
        ]
        
        filtered_response = []
        for resp in response:
            if resp.get("type") == "text":
                content = resp.get("content", "").lower()
                # Проверяем, не содержит ли ответ заблокированные фразы
                if not any(phrase in content for phrase in blocked_phrases):
                    filtered_response.append(resp)
                else:
                    logger.info(f"🚫 Заблокирован ответ: {resp.get('content')}")
            else:
                # Не текстовые ответы (карты, изображения) сохраняем как есть
                filtered_response.append(resp)
        
        return filtered_response

    async def enrich_request(
        self, user_id: str, current_analysis: Dict[str, Any], current_query: str
    ) -> Dict[str, Any]:
        
        # --- [НОВОЕ] Шаг 1: Фильтр "пустых" запросов в самом начале ---
        is_chitchat = (
            current_analysis.get("action") == "unknown" and 
            not (current_analysis.get("primary_entity") and current_analysis.get("primary_entity").get("name")) and
            not (current_analysis.get("secondary_entity") and current_analysis.get("secondary_entity").get("name")) and
            not current_analysis.get("attributes")
        )
        if is_chitchat:
            logger.info(f"[{user_id}] Запрос '{current_query}' распознан как 'small talk' ДО обогащения. Контекст не применяется.")
            return current_analysis # Возвращаем "пустой" анализ как есть

        # --- [ОСТАЛЬНАЯ ЛОГИКА, ЕСЛИ ЭТО НЕ "ПУСТОЙ" ЗАПРОС] ---
        
        last_history_entry = await self.get_latest_history(user_id)
        if not last_history_entry:
            return current_analysis

        # Если в текущем запросе есть новый явный объект, это смена контекста объекта.
        if current_analysis.get("primary_entity") and current_analysis.get("primary_entity").get("name"):
            logger.debug(f"[{user_id}] Обнаружена смена объекта на '{current_analysis['primary_entity']['name']}'. Сохраняем предыдущее действие.")
            final_analysis = current_analysis
            # Если в новом запросе не было действия, наследуем его из прошлого
            if final_analysis.get("action") == "unknown":
                final_analysis["action"] = last_history_entry.get("analysis", {}).get("action")
            
            logger.info(f"[{user_id}] ИТОГ ОБОГАЩЕНИЯ (смена объекта): {final_analysis}")
            return final_analysis

        # Если нового объекта нет, это уточнение для старого объекта.
        logger.debug(f"[{user_id}] Обнаружено уточнение для предыдущего контекста.")
        
        final_analysis = deepcopy(last_history_entry.get("analysis", {}))
        
        last_used_objects = last_history_entry.get("used_objects", [])
        if last_used_objects:
            # Проверяем, что в used_objects не гео-объекты (важно для "А где она обитает?")
            is_biological = last_used_objects[0].get("type") == "biological_entity"
            is_infrastructure = last_used_objects[0].get("type") == "infrastructure_entity" # Добавим проверку

            if is_biological or is_infrastructure:
                context_object = last_used_objects[0]
                final_analysis["primary_entity"] = { "name": context_object.get("name"), "type": "Biological" if is_biological else "Infrastructure" }

        # Применяем новые данные (действие, атрибуты) из текущего запроса
        if current_analysis.get("action") != "unknown":
            final_analysis["action"] = current_analysis["action"]
        final_analysis.setdefault("attributes", {}).update(current_analysis.get("attributes", {}))
        if current_analysis.get("secondary_entity"):
            final_analysis["secondary_entity"] = current_analysis["secondary_entity"]
        
        # Специальная логика для "Покажи их на карте"
        plural_markers = ["их", "эти", "всех"]
        is_plural_query = any(marker in current_query.lower() for marker in plural_markers)
        if final_analysis.get("action") == "show_map" and is_plural_query and last_used_objects and len(last_used_objects) > 1:
            final_analysis["used_objects_from_context"] = last_used_objects
            final_analysis["primary_entity"] = None

        logger.info(f"[{user_id}] ИТОГ ОБОГАЩЕНИЯ (уточнение): {final_analysis}")
        return final_analysis
  
    async def update_history(self, user_id: str, query: str, final_analysis: Dict[str, Any], response: list, used_objects: list = None):
        # Фильтруем ответы перед сохранением
        filtered_response = self._filter_blocked_responses(response)
        
        # Если после фильтрации ответов не осталось, не сохраняем в историю
        if not filtered_response:
            logger.info(f"[{user_id}] После фильтрации ответов не осталось. История не обновляется.")
            return
            
        primary_entity = final_analysis.get("primary_entity")
        if final_analysis.get("action") == "unknown" and (not primary_entity or not primary_entity.get("name")):
            logger.debug(f"[{user_id}] Пропуск сохранения нецелевого запроса в историю.")
            return

        history_entry = {
            "query": query,
            "analysis": final_analysis,
            "response": filtered_response,
            # [ИЗМЕНЕНИЕ] Шаг 2: Добавляем `used_objects` в запись истории
            "used_objects": used_objects or [] 
        }

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        
        updated_history = [history_entry] + history[:1]
        user_context['history'] = updated_history
        
        await self.context_manager.set_context(user_id, user_context)
        logger.info(f"[{user_id}] Контекст сохранен. Query: '{query}'")
    
        
    async def get_latest_history(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Возвращает самую последнюю запись из истории диалога."""
        if not self.context_manager.redis_client:
            return None
        
        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        
        return history[0] if history else None

  
