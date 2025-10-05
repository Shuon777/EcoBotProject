# --- НАЧАЛО ФАЙЛА: bot.py ---

import logging
import asyncio
import aiohttp # [НОВОЕ] Импортируем aiohttp
from aiogram import executor, types

from core.bot_instance import dp
from utils.settings_manager import get_user_settings

from handlers.general import register_general_handlers
from handlers.rasa_handler import RasaHandler, register_rasa_handlers
from handlers.gigachat_handler import GigaChatHandler
from utils.logging_config import setup_logging
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager

setup_logging()
logger = logging.getLogger(__name__)


async def on_startup(dispatcher):
    logger.info("Бот запускается...")
    
    # [НОВОЕ] Создаем единую сессию aiohttp и сохраняем ее в диспетчере
    dispatcher['aiohttp_session'] = aiohttp.ClientSession()
    
    try:
        qa = QueryAnalyzer()
        context_manager = RedisContextManager()
        if not await context_manager.check_connection():
            raise ConnectionError("Не удалось подключиться к Redis")
        dialogue_manager = DialogueManager(context_manager)
        
        # [ИЗМЕНЕНО] Получаем сессию из диспетчера
        session = dispatcher['aiohttp_session']
        
        # [ИЗМЕНЕНО] Передаем единую сессию в конструкторы обработчиков
        gigachat_h = GigaChatHandler(qa, dialogue_manager, session)
        rasa_h = RasaHandler(session)
        
        # --- Регистрация обработчиков ---
        register_general_handlers(dispatcher)
        register_rasa_handlers(dispatcher, rasa_h)
        
        dispatcher.register_callback_query_handler(
            gigachat_h.process_callback, 
            lambda c: not c.data.startswith('/') and c.data not in ["set_mode_rasa", "set_mode_gigachat", "toggle_fallback"]
        )
        
        @dispatcher.message_handler(content_types=types.ContentTypes.TEXT)
        async def handle_message_by_mode(message: types.Message):
            # Игнорируем команды, чтобы они обрабатывались отдельно, если будут
            if message.text.startswith('/'):
                return
            mode = get_user_settings(str(message.from_user.id)).get("mode", "rasa")

            if mode == "gigachat":
                await gigachat_h.process_message(message)
            else:
                await rasa_h.process_message(message)
        
        logger.info("Все обработчики успешно зарегистрированы.")

    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}", exc_info=True)

# [НОВОЕ] Добавляем функцию для корректного завершения работы
async def on_shutdown(dispatcher):
    logger.info("Бот останавливается...")
    # Закрываем сессию aiohttp
    await dispatcher['aiohttp_session'].close()
    logger.info("Aiohttp сессия закрыта.")

if __name__ == '__main__':
    # [ИЗМЕНЕНО] Передаем on_shutdown в executor
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)

# --- КОНЕЦ ФАЙЛА: bot.py ---