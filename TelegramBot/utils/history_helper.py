# TelegramBot/utils/history_helper.py
from utils.context_manager import RedisContextManager
import logging

logger = logging.getLogger("HistoryHelper")

async def save_interaction(user_id: str, query: str, response_text: str, context_manager: RedisContextManager):
    """Сохраняет простой лог диалога для Rewriter'а"""
    try:
        # 1. Получаем текущий контекст
        data = await context_manager.get_context(user_id)
        history = data.get("history", [])

        # 2. Формируем новую запись (упрощенно)
        # Мы сохраняем это так, чтобы Rewriter легко читал роли
        new_entry = {
            "query": query,
            "response_content": response_text
        }

        # 3. Добавляем в начало и ограничиваем (храним последние 10 диалогов)
        updated_history = [new_entry] + history[:10]
        data["history"] = updated_history

        # 4. Сохраняем обратно
        await context_manager.set_context(user_id, data)
    except Exception as e:
        logger.error(f"Failed to save history: {e}")