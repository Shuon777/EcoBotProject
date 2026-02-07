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
from handlers.inline_handler import register_inline_handlers
from utils.logging_config import setup_logging
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager
from utils.heartbeat import BotHeartbeat

setup_logging()
logger = logging.getLogger(__name__)


async def on_startup(dispatcher):
    logger.info("Бот запускается...")
    
    dispatcher['aiohttp_session'] = aiohttp.ClientSession()
    
    try:
        qa = QueryAnalyzer()
        context_manager = RedisContextManager()
        if not await context_manager.check_connection():
            raise ConnectionError("Не удалось подключиться к Redis")
        dialogue_manager = DialogueManager(context_manager)
        dispatcher['redis_client'] = context_manager.redis_client
        
        session = dispatcher['aiohttp_session']

        gigachat_h = GigaChatHandler(qa, dialogue_manager, session)
        rasa_h = RasaHandler(session)
        
        # --- Регистрация обработчиков ---
        register_general_handlers(dispatcher)
        register_rasa_handlers(dispatcher, rasa_h)
        register_inline_handlers(dispatcher)
        
        dispatcher.register_callback_query_handler(
            gigachat_h.process_callback, 
            lambda c: not c.data.startswith('/') and c.data not in ["set_mode_rasa", "set_mode_gigachat", "toggle_fallback"]
        )

        hb = BotHeartbeat(host='localhost', port=6379, db=2)
        async def heartbeat_task():
            logger.info("Цикл heartbeat запущен.") # Добавь лог для проверки
            while True:
                try:
                    await hb.ping()
                    # logger.debug("Heartbeat sent to Redis") # Раскомментируй для отладки
                except Exception as e:
                    logger.error(f"Ошибка Heartbeat: {e}")
                await asyncio.sleep(60)

        asyncio.create_task(heartbeat_task()) 
        
        @dispatcher.message_handler(content_types=types.ContentTypes.TEXT)
        async def handle_message_by_mode(message: types.Message):
            if message.text.startswith('/'):
                return
            mode = get_user_settings(str(message.from_user.id)).get("mode", "gigachat")

            if mode == "rasa":
                await rasa_h.process_message(message)
            else:
                await gigachat_h.process_message(message)

        logger.info("Все обработчики успешно зарегистрированы.")

    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}", exc_info=True)

async def on_shutdown(dispatcher):
    logger.info("Бот останавливается...")
    await dispatcher['aiohttp_session'].close()
    logger.info("Aiohttp сессия закрыта.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)

# --- КОНЕЦ ФАЙЛА: bot.py ---