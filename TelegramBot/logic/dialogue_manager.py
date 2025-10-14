
import logging
from typing import Dict, Any, Optional
from copy import deepcopy

from utils.context_manager import RedisContextManager

logger = logging.getLogger(__name__)

class DialogueManager:
    def __init__(self, context_manager: RedisContextManager):
        self.context_manager = context_manager

    async def enrich_request(
        self, user_id: str, current_analysis: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Обогащает текущий анализ данными из истории диалога.
        """
        # --- НАЧАЛО НОВОЙ ЛОГИКИ ---

        # ПРОВЕРКА №1: Смена темы (полностью пустой 'unknown')
        is_topic_change = (
            current_analysis.get("action") == "unknown" and
            not current_analysis.get("primary_entity") and
            not current_analysis.get("secondary_entity") and
            not current_analysis.get("attributes", {}) 
        )
        if is_topic_change:
            logger.debug(f"[{user_id}] Обнаружена смена темы. Контекст не применяется.")
            return current_analysis

        # ПРОВЕРКА №2: Новый полноценный запрос (с явным действием и сущностью)
        # В этом случае контекст тоже не нужен.
        if current_analysis.get("action") != "unknown" and current_analysis.get("primary_entity"):
             logger.debug(f"[{user_id}] Обнаружен новый полноценный запрос. Контекст не применяется.")
             return current_analysis

        # --- КОНЕЦ НОВОЙ ЛОГИКИ ---

        # Если мы дошли до сюда, значит, это УТОЧНЕНИЕ, и мы должны применить контекст.
        
        if not self.context_manager.redis_client:
            return current_analysis

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_analysis = history[0] if history else {}
        
        if not last_analysis:
            return current_analysis

        logger.debug(f"[{user_id}] НАЧАЛО ОБОГАЩЕНИЯ (уточняющий запрос). Текущий анализ: {current_analysis}")
        logger.debug(f"[{user_id}] Контекст: {last_analysis}")

        # --- НАЧАЛО ЛОГИКИ "УМНОГО" ОБОГАЩЕНИЯ ---
        
        # Начинаем с копии старого контекста
        final_analysis = deepcopy(last_analysis)
        
        # Если пришла новая primary_entity, она заменяет старую, но action и атрибуты остаются!
        if current_analysis.get("primary_entity"):
            final_analysis["primary_entity"] = current_analysis["primary_entity"]
            # ВАЖНО: При смене основной сущности, старые атрибуты и secondary_entity,
            # относящиеся к прошлой, должны быть сброшены.
            final_analysis["attributes"] = current_analysis.get("attributes", {})
            final_analysis["secondary_entity"] = current_analysis.get("secondary_entity")
            logger.debug(f"[{user_id}] Сменили primary_entity. Атрибуты и secondary_entity сброшены.")
        else:
            # Если новой primary_entity нет, то просто объединяем атрибуты и secondary_entity
            final_analysis.get("attributes", {}).update(current_analysis.get("attributes", {}))
            if current_analysis.get("secondary_entity"):
                final_analysis["secondary_entity"] = current_analysis.get("secondary_entity")
        
        # Явный action из нового запроса всегда имеет приоритет
        if current_analysis.get("action") != "unknown":
            final_analysis["action"] = current_analysis["action"]
            
        # --- КОНЕЦ ЛОГИКИ "УМНОГО" ОБОГАЩЕНИЯ ---

        logger.info(f"[{user_id}] ИТОГ ОБОГАЩЕНИЯ: {final_analysis}")
        return final_analysis
    
    async def update_history(self, user_id: str, final_analysis: Dict[str, Any]):
        # ... (этот метод остается без изменений)
        primary_entity = final_analysis.get("primary_entity")
        # --- ИСПРАВЛЕНИЕ: Не сохраняем в историю "пустые" unknown запросы ---
        if final_analysis.get("action") == "unknown" and (not primary_entity or not primary_entity.get("name")):
             logger.debug(f"[{user_id}] Пропуск сохранения нецелевого запроса в историю.")
             return

        if not primary_entity or not primary_entity.get("name"):
            logger.debug(f"[{user_id}] Пропуск сохранения в историю: в анализе нет `primary_entity`.")
            return

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        
        updated_history = [final_analysis] + history[:1]
        user_context['history'] = updated_history
        
        await self.context_manager.set_context(user_id, user_context)
        logger.info(f"[{user_id}] Контекст сохранен: {final_analysis}")

    async def get_comparison_pair(
        self, user_id: str, current_analysis: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """
        Проверяет, можно ли предложить сравнение на основе текущего и предыдущего запроса.
        """
        if not self.context_manager.redis_client:
            return None

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_analysis = history[0] if history else {}

        current_entity = current_analysis.get("primary_entity", {})
        last_entity = last_analysis.get("primary_entity", {})

        if (current_analysis.get("action") in ["describe", "show_image"] and
                current_entity.get("type") == "Biological" and
                last_entity.get("type") == "Biological" and
                current_entity.get("name") and last_entity.get("name") and
                current_entity.get("name") != last_entity.get("name")):
            
            logger.info(f"[{user_id}] Найдена пара для сравнения: {last_entity['name']} и {current_entity['name']}")
            return {"object1": last_entity["name"], "object2": current_entity["name"]}
        
        return None