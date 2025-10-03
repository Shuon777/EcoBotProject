# --- НАЧАЛО ФАЙЛА: handlers/rasa_handler.py ---

import aiohttp
import logging
from aiogram import types, Dispatcher
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import RASA_WEBHOOK_URL, DEFAULT_TIMEOUT
from utils.bot_utils import normalize_message, send_normalized_message

logger = logging.getLogger(__name__)

class RasaHandler:
    async def process_message(self, message: types.Message):
        """Обрабатывает текстовое сообщение в режиме Rasa."""
        user_id = str(message.from_user.id)
        query = message.text
        
        await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
        payload = {"sender": user_id, "message": query, "metadata": {}}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(RASA_WEBHOOK_URL, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status == 200:
                        rasa_responses = await resp.json()
                        for r in (rasa_responses or []):
                            norm = normalize_message(r)
                            await send_normalized_message(message, norm)
                    else:
                        logger.error(f"Ошибка при обращении к Rasa: {resp.status} - {await resp.text()}")
                        await message.answer("Ошибка при обращении к Rasa.")
        except Exception as e:
            logger.error(f"Не удалось подключиться к серверу Rasa: {e}")
            await message.answer("Мой основной обработчик Rasa сейчас недоступен.")

    async def process_callback(self, callback_query: types.CallbackQuery):
        """Обрабатывает нажатие на инлайн-кнопку от Rasa."""
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        message = callback_query.message
        
        if data != '/search_more':
            try:
                await message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.warning(f"Не удалось убрать клавиатуру: {e}")

        await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
        payload = {"sender": user_id, "message": data, "metadata": {}}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(RASA_WEBHOOK_URL, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status == 200:
                        rasa_responses = await resp.json()
                        for r in (rasa_responses or []):
                            norm = normalize_message(r)
                            if data == '/search_more' and norm.get('buttons'):
                                inline_markup = InlineKeyboardMarkup(row_width=1)
                                for row in norm.get("buttons", []):
                                    buttons_in_row = [InlineKeyboardButton(btn["text"], callback_data=btn.get("callback_data")) for btn in row]
                                    inline_markup.row(*buttons_in_row)
                                await message.edit_text(norm.get('text', 'Выберите вариант:'), reply_markup=inline_markup)
                            else:
                                await send_normalized_message(message, norm)
        except Exception as e:
             logger.error(f"Ошибка при обработке callback для Rasa: {e}")
             await message.answer("Произошла ошибка при обработке вашего выбора.")

        await callback_query.answer()

# --- Регистрация обработчиков ---

def register_rasa_handlers(dp: Dispatcher, rasa_handler: RasaHandler):
    """Регистрирует обработчики для режима Rasa."""
    
    # Регистрируем обработчик для инлайн-кнопок, начинающихся с '/'
    dp.register_callback_query_handler(rasa_handler.process_callback, lambda c: c.data.startswith('/'))

# --- КОНЕЦ ФАЙЛА: handlers/rasa_handler.py ---