# --- НАЧАЛО ФАЙЛА TelegramBot/utils/bot_utils.py ---
from aiogram import types
import re
import logging
import html  # <--- ДОБАВЛЕНО

logger = logging.getLogger(__name__)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

async def send_long_message(message: types.Message, text: str, parse_mode: str = None, reply_markup=None):
    """
    Отправляет длинное сообщение, "красиво" разбивая его на части.
    Старается не разрывать слова и строки.
    Клавиатура (reply_markup) прикрепляется к последнему сообщению.
    """
    if not text or not text.strip():
        logger.warning(f"Попытка отправить пустое сообщение для чата {message.chat.id}. Отправка отменена.")
        return

    message_chunks = []
    
    while len(text) > 0:
        if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            message_chunks.append(text)
            break

        chunk = text[:TELEGRAM_MAX_MESSAGE_LENGTH]
        split_pos = -1

        possible_split = chunk.rfind('\n')
        if possible_split != -1:
            split_pos = possible_split
        else:
            possible_split = chunk.rfind(' ')
            if possible_split != -1:
                split_pos = possible_split

        if split_pos == -1:
            split_pos = TELEGRAM_MAX_MESSAGE_LENGTH
        
        message_chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()

    for i in range(len(message_chunks) - 1):
        if message_chunks[i]:
            await message.answer(message_chunks[i], parse_mode=parse_mode, disable_web_page_preview=True)
    
    if message_chunks and message_chunks[-1]:
        await message.answer(message_chunks[-1], parse_mode=parse_mode, disable_web_page_preview=True, reply_markup=reply_markup)

def normalize_message(msg: dict) -> dict:
    """
    Приводит любое сообщение от Rasa к единой структуре.
    """
    result = {"text": None, "image": None, "file": None, "buttons": [], "buttons_type": None, "parse_mode": None}
    
    if "text" in msg:
        result["text"] = msg["text"]
    if "image" in msg:
        result["image"] = msg["image"]
    if "attachment" in msg and msg["attachment"].get("type") == "file":
        result["file"] = msg["attachment"]["payload"]["url"]
    
    if "custom" in msg:
        custom = msg["custom"]
        if "text" in custom: result["text"] = custom["text"]
        if "photo" in custom: result["image"] = custom["photo"]
        if "parse_mode" in custom: result["parse_mode"] = custom["parse_mode"]
        if "reply_markup" in custom:
            markup = custom["reply_markup"]
            if "inline_keyboard" in markup:
                result["buttons"] = markup["inline_keyboard"]
                result["buttons_type"] = "inline"
            elif "keyboard" in markup:
                result["buttons"] = markup["keyboard"]
                result["buttons_type"] = "reply"
    return result

async def send_normalized_message(message: types.Message, norm: dict):
    """
    Отправляет нормализованное сообщение от Rasa.
    """
    if "file" in norm and norm["file"]:
        await message.answer_document(norm["file"])
        return

    markup = None
    if norm["buttons"]:
        if norm["buttons_type"] == "inline":
            inline_markup = types.InlineKeyboardMarkup(row_width=1)
            for row in norm["buttons"]:
                buttons_in_row = [
                    types.InlineKeyboardButton(
                        btn["text"], 
                        url=btn.get("url"), 
                        callback_data=btn.get("callback_data")
                    ) for btn in row
                ]
                inline_markup.row(*buttons_in_row)
            markup = inline_markup
        elif norm["buttons_type"] == "reply":
            reply_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            for row in norm["buttons"]:
                reply_markup.row(*[types.KeyboardButton(btn["text"]) for btn in row])
            markup = reply_markup

    if norm["image"]:
        if norm["text"] and len(norm["text"]) > 1024:
            sent_message = await message.answer_photo(norm["image"])
            if "context" in norm:
                sent_message.context = norm["context"]
            await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"), reply_markup=markup)
        else:
            sent_message = await message.answer_photo(norm["image"], caption=norm["text"], reply_markup=markup, parse_mode=norm.get("parse_mode"))
            if "context" in norm:
                sent_message.context = norm["context"]
        return

    if norm["text"]:
        if markup:
            sent_message = await message.answer(norm["text"], parse_mode=norm.get("parse_mode"), disable_web_page_preview=True, reply_markup=markup)
        else:
            await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"), reply_markup=markup)
            return
        
        if "context" in norm:
            sent_message.context = norm["context"]
    elif markup:
         sent_message = await message.answer("Выберите вариант:", reply_markup=markup)
         if "context" in norm:
             sent_message.context = norm["context"]

def create_structured_response(api_data: dict, responses: list) -> list:
    if not isinstance(api_data, dict):
        return responses

    used_objects = api_data.get("used_objects", [])
    if responses and used_objects:
        responses[0]['used_objects'] = used_objects
        logging.getLogger(__name__).info(f"К ответу прикреплено used_objects: {len(used_objects)} шт.")
        
    return responses

def escape_markdown(text: str) -> str:
    """
    Экранирует специальные символы Markdown V2 в тексте.
    """
    if not text:
        return ""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

def convert_llm_markdown_to_html(text: str) -> str:
    """
    Преобразует базовую Markdown-разметку LLM (**, ###) в HTML,
    понятный Telegram, и экранирует спецсимволы.
    """
    if not text:
        return ""
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'###\s*(.+)', r'<b>\1</b>', text)
    return text