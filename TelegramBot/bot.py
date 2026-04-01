import os
import logging
import asyncio
import aiohttp
from aiogram import executor, types
from dotenv import load_dotenv

# Базовые сущности aiogram
from core.bot_instance import dp, bot

# Утилиты и менеджеры
from utils.settings_manager import get_user_settings
from utils.logging_config import setup_logging
from utils.context_manager import RedisContextManager
from utils.heartbeat import BotHeartbeat

# Наша новая архитектура
from logic.DialogSystem.orchestrator import DialogueSystem
from logic.DialogSystem.schemas import UserRequest, SystemResponse

# Старые хендлеры (Rasa и общие настройки)
from handlers.general import register_general_handlers
from handlers.rasa_handler import RasaHandler, register_rasa_handlers
from handlers.inline_handler import register_inline_handlers

# Утилита для отправки (Рендерер) - напишем её ниже
from utils.bot_utils import send_long_message, convert_llm_markdown_to_html

setup_logging()
load_dotenv()
logger = logging.getLogger("BotApp")

async def on_startup(dispatcher):
    logger.info("🚀 Запуск обновленного Эко-бота...")
    
    # 1. Инициализация сессии
    session = aiohttp.ClientSession()
    dispatcher['aiohttp_session'] = session
    
    try:
        # 2. Инициализация Redis и Контекста
        redis_host = os.getenv('REDIS_PATH', 'localhost')
        context_manager = RedisContextManager(host=redis_host, port=6379, db=0)
        if not await context_manager.check_connection():
            raise ConnectionError("Не удалось подключиться к Redis")
        dispatcher['context_manager'] = context_manager
        
        # 3. Инициализация "Мозга" (Dialogue System)
        # Мы передаем сессию и провайдера (из .env)
        llm_provider = os.getenv("LLM_PROVIDER", "qwen")
        ds = DialogueSystem(provider=llm_provider, session=session)
        dispatcher['ds'] = ds
        
        # 4. Инициализация Rasa (для режима совместимости)
        rasa_h = RasaHandler(session)
        dispatcher['rasa_handler'] = rasa_h
        
        # 5. Регистрация стандартных хендлеров aiogram
        register_general_handlers(dispatcher)
        register_rasa_handlers(dispatcher, rasa_h)
        register_inline_handlers(dispatcher)
        
        # 6. Heartbeat (никуда не делся)
        hb = BotHeartbeat(host=redis_host, port=6379, db=2)
        async def heartbeat_loop():
            while True:
                try:
                    await hb.ping()
                except Exception as e:
                    logger.error(f"Heartbeat Error: {e}")
                await asyncio.sleep(60)
        asyncio.create_task(heartbeat_loop())
        
        logger.info(f"✅ Система готова. Провайдер: {llm_provider}")
        
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка на старте: {e}", exc_info=True)
        raise

# --- ГЛАВНЫЙ ОБРАБОТЧИК СООБЩЕНИЙ ---

@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def handle_main_logic(message: types.Message):
    """Единая точка входа для всех текстовых запросов"""
    if message.text.startswith('/'):
        return

    user_id = str(message.from_user.id)
    user_settings = get_user_settings(user_id)
    mode = user_settings.get("mode", "gigachat") # gigachat теперь синоним нашей DS

    # ВАРИАНТ А: Режим RASA
    if mode == "rasa":
        rasa_h = dp['rasa_handler']
        await rasa_h.process_message(message)
        return

    # ВАРИАНТ Б: Режим Dialogue System (Qwen/GigaChat)
    ds: DialogueSystem = dp['ds']
    context_manager: RedisContextManager = dp['context_manager']
    
    # 1. Получаем историю из Redis (превращаем в список ролей для LLM)
    history_data = await context_manager.get_context(user_id)
    history = history_data.get("history", [])
    
    # Форматируем историю для Rewriter (упрощенно)
    formatted_context = []
    for entry in history[-5:]: # берем последние 5 сообщений
        formatted_context.append({"role": "user", "content": entry.get("query", "")})
        # Берем текст из первого ответа в списке ответов
        resps = entry.get("response", [])
        if resps:
            formatted_context.append({"role": "assistant", "content": resps[0].get("content", "")})

    # 2. Создаем стандартизированный запрос
    request = UserRequest(
        user_id=user_id,
        query=message.text,
        context=formatted_context,
        settings=user_settings
    )

    # 3. Вызываем "Мозг"
    # Отправляем статус "typing" в телеграм
    await bot.send_chat_action(user_id, types.ChatActions.TYPING)
    response: SystemResponse = await ds.process_request(request)

    # 4. Рендерим ответ (Отправка в TG)
    await render_system_response(message, response)

    # 5. Сохраняем в историю (через старый DialogueManager или напрямую)
    # Здесь важно сохранить не только текст, но и финальный анализ для контекста
    # (Это мы допишем в финальном шаге Фазы 3)

async def render_system_response(message: types.Message, response: SystemResponse):
    """Превращает SystemResponse в реальные сообщения Telegram"""
    
    # Сборка клавиатуры
    markup = None
    if response.buttons:
        markup = types.InlineKeyboardMarkup()
        for row in response.buttons:
            btns = [types.InlineKeyboardButton(text=b['text'], callback_data=b.get('callback_data'), url=b.get('url')) for b in row]
            markup.row(*btns)

    # Логика отправки по типам
    if response.response_type == "image" and response.media_url:
        await message.answer_photo(photo=response.media_url, caption=response.text, reply_markup=markup)
    
    elif response.response_type == "map" and response.media_url:
        # Если есть интерактивная карта, добавляем кнопку
        await message.answer_photo(photo=response.media_url, caption=response.text, reply_markup=markup)
        
    else:
        # Обычный текст (с поддержкой длинных сообщений)
        html_text = convert_llm_markdown_to_html(response.text)
        await send_long_message(message, html_text, parse_mode="HTML", reply_markup=markup)

async def on_shutdown(dispatcher):
    logger.info("🛑 Остановка бота...")
    await dispatcher['aiohttp_session'].close()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup, on_shutdown=on_shutdown)