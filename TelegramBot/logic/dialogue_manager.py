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
        Это ключевая функция для обработки уточняющих запросов.
        """
        if not self.context_manager.redis_client:
            return current_analysis

        user_context = await self.context_manager.get_context(user_id)
        history = user_context.get("history", [])
        last_analysis = history[0] if history else {}
        
        if not last_analysis:
            logger.debug(f"[{user_id}] Контекст пуст, обогащение не требуется.")
            return current_analysis

        # Полностью переписана логика обогащения
        logger.debug(f"[{user_id}] НАЧАЛО ОБОГАЩЕНИЯ. Текущий анализ: {current_analysis}")
        logger.debug(f"[{user_id}] Контекст: {last_analysis}")

        # Начинаем с глубокой копии последнего контекста, это наша основа
        final_analysis = deepcopy(last_analysis)

        # 1. Обновляем действие (action), если в новом запросе оно осмысленное
        current_action = current_analysis.get("action")
        if current_action and current_action != "unknown":
            final_analysis["action"] = current_action
            logger.debug(f"[{user_id}] Обновили `action` на '{current_action}' из нового запроса.")

        # 2. Обновляем primary_entity, если в новом запросе оно есть
        current_primary_entity = current_analysis.get("primary_entity")
        if current_primary_entity and current_primary_entity.get("name"):
            final_analysis["primary_entity"] = current_primary_entity
            logger.debug(f"[{user_id}] Обновили `primary_entity` на '{current_primary_entity}' из нового запроса.")
        
        # 3. Обновляем secondary_entity, если в новом запросе оно есть
        current_secondary_entity = current_analysis.get("secondary_entity")
        if current_secondary_entity and current_secondary_entity.get("name"):
            final_analysis["secondary_entity"] = current_secondary_entity
            logger.debug(f"[{user_id}] Обновили `secondary_entity` на '{current_secondary_entity}' из нового запроса.")

        # 4. ОБЪЕДИНЯЕМ АТРИБУТЫ: новые атрибуты перезаписывают старые
        current_attributes = current_analysis.get("attributes", {})
        if current_attributes:
            if "attributes" not in final_analysis:
                final_analysis["attributes"] = {}
            final_analysis["attributes"].update(current_attributes)
            logger.debug(f"[{user_id}] Объединили атрибуты. Результат: {final_analysis['attributes']}")

        logger.info(f"[{user_id}] ИТОГ ОБОГАЩЕНИЯ: {final_analysis}")
        return final_analysis

    async def update_history(self, user_id: str, final_analysis: Dict[str, Any]):
        """
        Сохраняет последний успешный анализ в историю пользователя.
        """
        primary_entity = final_analysis.get("primary_entity")
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