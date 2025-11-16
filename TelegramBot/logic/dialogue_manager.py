
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
        self, user_id: str, current_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обогащает текущий анализ данными из истории диалога, корректно
        обрабатывая уточнения, смену действия и смену объекта.
        """
        # [ИСПРАВЛЕНИЕ]
        # Мы считаем запрос "новым" и не требующим контекста, только если
        # он сам по себе содержит и действие, и объект.
        # Запрос "А эдельвейс?" сюда не попадет, так как его action="unknown".
        is_new_full_request = (
            current_analysis.get("action") != "unknown" and
            current_analysis.get("primary_entity") and
            current_analysis.get("primary_entity").get("name")
        )
        if is_new_full_request:
            logger.debug(f"[{user_id}] Обнаружен новый полноценный запрос. Контекст не применяется.")
            return current_analysis

        # Если истории нет, обогащать нечем.
        last_history_entry = await self.get_latest_history(user_id)
        if not last_history_entry:
            return current_analysis

        logger.debug(f"[{user_id}] Запрос является уточнением. Применяем контекст из истории.")
        
        # 1. Берем за основу анализ из ПРЕДЫДУЩЕГО шага
        last_analysis = last_history_entry.get("analysis", {})
        final_analysis = deepcopy(last_analysis)
        
        # 2. "Накатываем" поверх него изменения из ТЕКУЩЕГО "сырого" анализа
        
        # Если в текущем запросе определился новый объект, он главнее старого.
        if current_analysis.get("primary_entity") and current_analysis.get("primary_entity").get("name"):
            final_analysis["primary_entity"] = current_analysis["primary_entity"]
        
        # Если определился новый action, он главнее старого.
        if current_analysis.get("action") != "unknown":
            final_analysis["action"] = current_analysis["action"]
        
        # Атрибуты и secondary_entity просто добавляются/обновляются.
        if "attributes" in final_analysis:
            final_analysis["attributes"].update(current_analysis.get("attributes", {}))
        else:
            final_analysis["attributes"] = current_analysis.get("attributes", {})
        
        if current_analysis.get("secondary_entity"):
            final_analysis["secondary_entity"] = current_analysis.get("secondary_entity")
        
        logger.info(f"[{user_id}] ИТОГ ОБОГАЩЕНИЯ: {final_analysis}")
        return final_analysis
    
    async def update_history(self, user_id: str, query: str, final_analysis: Dict[str, Any], response: list):
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
            "response": filtered_response  # ← Сохраняем ОТФИЛЬТРОВАННЫЙ ответ
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

  
