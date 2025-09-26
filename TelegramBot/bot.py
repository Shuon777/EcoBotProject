# --- НАЧАЛО ФАЙЛА TelegramBot/bot.py ---
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
import logging
from query_analyze import QueryAnalyzer
from handlers import handle_intent
from bot_utils import normalize_message, send_normalized_message, send_long_message
from config import BOT_TOKEN, RASA_WEBHOOK_URL, DEFAULT_TIMEOUT
from settings_manager import get_user_settings, update_user_settings

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    qa = QueryAnalyzer()
    logger.info("QueryAnalyzer (GigaChat) успешно инициализирован.")
except Exception as e:
    qa = None
    logger.critical(f"Не удалось инициализировать QueryAnalyzer: {e}")

def get_user_mode(user_id: str) -> str:
    return get_user_settings(user_id).get("mode", "rasa")

def get_user_fallback(user_id: str) -> bool:
    return get_user_settings(user_id).get("gigachat_fallback", False)

def create_settings_keyboard(user_id: str) -> InlineKeyboardMarkup:
    current_mode = get_user_mode(user_id)
    rasa_button_text = "✅ Режим: Rasa" if current_mode == "rasa" else "Режим: Rasa"
    gigachat_button_text = "✅ Режим: GigaChat" if current_mode == "gigachat" else "Режим: GigaChat"
    
    fallback_status = "✅ Вкл" if get_user_fallback(user_id) else "❌ Выкл"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(rasa_button_text, callback_data="set_mode_rasa"),
        InlineKeyboardButton(gigachat_button_text, callback_data="set_mode_gigachat")
    )
    keyboard.add(
        InlineKeyboardButton(f"Дополнять GigaChat: {fallback_status}", callback_data="toggle_fallback")
    )
    return keyboard

async def get_bot_response(query: str, user_id: str, mode: str, debug_mode: bool = False) -> list:
    if mode == "rasa":
        payload = {"sender": user_id, "message": query, "metadata": {"debug_mode": debug_mode}}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(RASA_WEBHOOK_URL, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                    if resp.status == 200:
                        rasa_responses = await resp.json()
                        return rasa_responses or [{"type": "text", "content": "Извините, я не смог обработать ваш запрос."}]
                    else:
                        logger.error(f"Ошибка при обращении к Rasa: {resp.status} - {await resp.text()}")
                        return [{"type": "text", "content": "Ошибка при обращении к Rasa."}]
        except Exception as e:
            logger.error(f"Не удалось подключиться к серверу Rasa: {e}")
            return [{"type": "text", "content": "Мой основной обработчик Rasa сейчас недоступен."}]
    else: 
        if not qa:
            return [{"type": "text", "content": "Извините, режим GigaChat временно недоступен."}]
        
        result = qa.query_analyzer(query)
        if result.get("success"):
            r = result["result"]
            if not r.get("can_fulfill", True):
                return [{"type": "text", "content": "Я понял ваш запрос, но не могу его выполнить с указанными признаками."}]
            
            return await handle_intent(r, user_id=user_id, original_query=query, debug_mode=debug_mode)
        else:
            return [{"type": "text", "content": f"Ошибка анализа: {result.get('error','неизвестно')}"}]

main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True).add(types.KeyboardButton("⚙ Настройки"))

@dp.message_handler(commands=["start"])
async def handle_start(message: types.Message):
    user_id = str(message.from_user.id)
    mode_name = "Rasa" if get_user_mode(user_id) == "rasa" else "GigaChat"
    await message.answer(
        f"Добро пожаловать в эко-ассистент Байкала 🌿\n"
        f"Текущий режим: {mode_name}\n"
        f"Сменить можно в ⚙ Настройки\n\n"
        f"Чем могу помочь?",
        reply_markup=main_keyboard
    )

@dp.message_handler(lambda message: message.text == "⚙ Настройки")
async def handle_settings(message: types.Message):
    user_id = str(message.from_user.id)
    keyboard = create_settings_keyboard(user_id)
    await message.answer("Меню настроек:", reply_markup=keyboard)

@dp.callback_query_handler()
async def process_callback_buttons(callback_query: types.CallbackQuery):
    user_id = str(callback_query.from_user.id)
    data = callback_query.data

    if data == "set_mode_rasa":
        update_user_settings(user_id, {"mode": "rasa"})
        await callback_query.answer("Режим переключен на Rasa")
    elif data == "set_mode_gigachat":
        update_user_settings(user_id, {"mode": "gigachat"})
        await callback_query.answer("Режим переключен на GigaChat")
    elif data == "toggle_fallback":
        new_state = not get_user_fallback(user_id)
        update_user_settings(user_id, {"gigachat_fallback": new_state})
        await callback_query.answer(f"Режим дополнения GigaChat {'включен' if new_state else 'выключен'}")

    keyboard = create_settings_keyboard(user_id)
    try:
        if callback_query.message.reply_markup != keyboard:
            await callback_query.message.edit_reply_markup(keyboard)
    except Exception as e:
        logger.warning(f"Не удалось обновить клавиатуру: {e}")

@dp.message_handler(content_types=types.ContentTypes.TEXT)
async def handle_message(message: types.Message):
    user_id = str(message.chat.id)
    query = message.text
    mode = get_user_mode(user_id)

    await bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
    responses = await get_bot_response(query, user_id, mode, debug_mode=False)

    for resp_data in responses:
        if mode == "rasa":
            norm = normalize_message(resp_data)
            await send_normalized_message(message, norm)
        else:
            if resp_data.get("type") == "text":
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            elif resp_data.get("type") == "image":
                await message.answer_photo(resp_data["content"])
            elif resp_data.get("type") == "map":
                kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                if "caption" in resp_data and len(resp_data["caption"]) > 1024:
                    await message.answer_photo(photo=resp_data["static"], reply_markup=kb)
                    await send_long_message(message, resp_data["caption"])
                else:
                    await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", "Карта"), reply_markup=kb)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
# --- КОНЕЦ ФАЙЛА TelegramBot/bot.py ---