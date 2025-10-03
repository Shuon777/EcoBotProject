# --- НАЧАЛО ФАЙЛА: bot.py ---

import logging
import asyncio
from aiogram import executor, types

from core.bot_instance import dp
from utils.settings_manager import get_user_settings
from utils.logging_config import setup_logging


# Импортируем наши обработчики
from handlers.general import register_general_handlers
from handlers.rasa_handler import RasaHandler, register_rasa_handlers
from handlers.gigachat_handler import GigaChatHandler

# Импортируем зависимости для инициализации
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager

# --- Настройка логирования ---
setup_logging()
logger = logging.getLogger(__name__)


async def on_startup(dispatcher):
    """Выполняется при старте бота. Инициализирует и регистрирует все обработчики."""
    logger.info("Бот запускается...")
    
    try:
        # --- Инициализация всех зависимостей ---
        qa = QueryAnalyzer()
        context_manager = RedisContextManager()
        if not context_manager.redis_client:
            raise ConnectionError("Не удалось подключиться к Redis")
        dialogue_manager = DialogueManager(context_manager)
        
        # --- Создание экземпляров обработчиков ---
        gigachat_h = GigaChatHandler(qa, dialogue_manager)
        rasa_h = RasaHandler()
        
        # --- Регистрация обработчиков ---
        
        # 1. Общие хендлеры (/start, Настройки)
        register_general_handlers(dispatcher)
        
        # 2. Хендлеры для Rasa (текст обрабатывается в общем, кнопки - отдельно)
        register_rasa_handlers(dispatcher, rasa_h)
        
        # [# НОВОЕ] 3. Регистрируем обработчик для кнопок GigaChat
        # Он будет ловить все callback'и, которые не начинаются с '/' (для Rasa) и не являются кнопками настроек
        dispatcher.register_callback_query_handler(
            gigachat_h.process_callback, 
            lambda c: not c.data.startswith('/') and c.data not in ["set_mode_rasa", "set_mode_gigachat", "toggle_fallback"]
        )
        
        # 4. Регистрируем главный обработчик текстовых сообщений
        @dispatcher.message_handler(content_types=types.ContentTypes.TEXT)
        async def handle_message_by_mode(message: types.Message):
            if message.text.startswith('/'): # Пропускаем команды, для них есть свои хендлеры
                return

            mode = get_user_settings(str(message.from_user.id)).get("mode", "rasa")

            if mode == "gigachat":
                await gigachat_h.process_message(message)
            else:
                await rasa_h.process_message(message)
        
        logger.info("Все обработчики успешно зарегистрированы.")

    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {e}", exc_info=True)


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)

# --- КОНЕЦ ФАЙЛА: bot.py ---