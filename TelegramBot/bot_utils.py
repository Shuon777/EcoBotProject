# --- НАЧАЛО ФАЙЛА TelegramBot/bot_utils.py ---
from aiogram import types
import logging

logger = logging.getLogger(__name__)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

async def send_long_message(message: types.Message, text: str, parse_mode: str = None):
    """
    Отправляет длинное сообщение, разбивая его на части, если необходимо.
    """
    if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        await message.answer(text, parse_mode=parse_mode, disable_web_page_preview=True)
        return

    parts = text.split('\n')
    current_message = ""
    for part in parts:
        # Проверяем, не превысит ли добавление следующей части лимит
        if len(current_message) + len(part) + 1 > TELEGRAM_MAX_MESSAGE_LENGTH:
            await message.answer(current_message, parse_mode=parse_mode, disable_web_page_preview=True)
            current_message = part
        else:
            if current_message:  # Добавляем перенос строки, если это не первая часть
                current_message += "\n"
            current_message += part

    # Отправляем оставшуюся часть
    if current_message:
        await message.answer(current_message, parse_mode=parse_mode, disable_web_page_preview=True)

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
        if "text" in custom:
            result["text"] = custom["text"]
        if "photo" in custom:
            result["image"] = custom["photo"]
        if "parse_mode" in custom:
            result["parse_mode"] = custom["parse_mode"]
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
    Отправляет нормализованное сообщение от Rasa, используя `send_long_message` для текста.
    """
    if "file" in norm and norm["file"]:
        await message.answer_document(norm["file"])
        return

    markup = None
    if norm["buttons"]:
        if norm["buttons_type"] == "inline":
            inline_markup = types.InlineKeyboardMarkup()
            for row in norm["buttons"]:
                inline_markup.row(*[types.InlineKeyboardButton(btn["text"], url=btn.get("url")) for btn in row])
            markup = inline_markup
        elif norm["buttons_type"] == "reply":
            reply_markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            for row in norm["buttons"]:
                reply_markup.row(*[types.KeyboardButton(btn["text"]) for btn in row])
            markup = reply_markup

    if norm["image"]:
        # Для фото текст отправляется как caption, у которого лимит меньше (1024),
        # поэтому длинный текст нужно отправить отдельно.
        if norm["text"] and len(norm["text"]) > 1024:
            await message.answer_photo(norm["image"], reply_markup=markup)
            await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"))
        else:
            await message.answer_photo(norm["image"], caption=norm["text"], reply_markup=markup, parse_mode=norm.get("parse_mode"))
        return

    if norm["text"]:
        await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"))

    if markup and not norm["text"] and not norm["image"]:
         await message.answer("Выберите вариант:", reply_markup=markup)
# --- КОНЕЦ ФАЙЛА TelegramBot/bot_utils.py ---