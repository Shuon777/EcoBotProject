# --- НАЧАЛО ФАЙЛА TelegramBot/utils/bot_utils.py ---
from aiogram import types
import logging

logger = logging.getLogger(__name__)
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

async def send_long_message(message: types.Message, text: str, parse_mode: str = None, reply_markup=None):
    """
    Отправляет длинное сообщение, "красиво" разбивая его на части.
    Старается не разрывать слова и строки.
    Клавиатура (reply_markup) прикрепляется к последнему сообщению.
    """
    # Проверка на пустой текст остается
    if not text or not text.strip():
        logger.warning(f"Попытка отправить пустое сообщение для чата {message.chat.id}. Отправка отменена.")
        return

    message_chunks = []
    
    # ---> [НАЧАЛО НОВОЙ "УМНОЙ" ЛОГИКИ РАЗБИЕНИЯ]
    while len(text) > 0:
        if len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
            message_chunks.append(text)
            break

        # Берем срез текста, не превышающий лимит
        chunk = text[:TELEGRAM_MAX_MESSAGE_LENGTH]
        
        # Ищем лучшую точку для разрыва, двигаясь от конца среза к началу
        split_pos = -1

        # 1. Предпочитаем разрыв по переносу строки
        possible_split = chunk.rfind('\n')
        if possible_split != -1:
            split_pos = possible_split
        
        # 2. Если нет переноса, ищем последний пробел
        else:
            possible_split = chunk.rfind(' ')
            if possible_split != -1:
                split_pos = possible_split

        # 3. Если не найдено ни переносов, ни пробелов (очень длинное слово),
        # то вынужденно режем по лимиту.
        if split_pos == -1:
            split_pos = TELEGRAM_MAX_MESSAGE_LENGTH
        
        # Добавляем кусок в список и готовим оставшийся текст для следующей итерации
        message_chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip() # .lstrip() убирает пробелы/переносы в начале следующего куска

    # ---> [КОНЕЦ НОВОЙ ЛОГИКИ]

    # Отправка сообщений остается прежней
    # Отправляем все части, кроме последней
    for i in range(len(message_chunks) - 1):
        if message_chunks[i]:
            await message.answer(message_chunks[i], parse_mode=parse_mode, disable_web_page_preview=True)
    
    # Отправляем последнюю часть с клавиатурой (если она есть)
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
    
    # Поддержка формата custom от Rasa
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
    Отправляет нормализованное сообщение от Rasa, используя `send_long_message` для текста
    и корректно обрабатывая клавиатуры.
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
        # Если подпись к картинке слишком длинная, отправляем ее отдельным сообщением
        if norm["text"] and len(norm["text"]) > 1024:
            sent_message = await message.answer_photo(norm["image"])
            # Сохраняем context если есть
            if "context" in norm:
                sent_message.context = norm["context"]
            # Отправляем длинный текст с кнопками уже после фото
            await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"), reply_markup=markup)
        else:
            sent_message = await message.answer_photo(norm["image"], caption=norm["text"], reply_markup=markup, parse_mode=norm.get("parse_mode"))
            # Сохраняем context если есть
            if "context" in norm:
                sent_message.context = norm["context"]
        return

    if norm["text"]:
        # Используем send_long_message для текста и сохраняем context
        if markup:
            # Если есть клавиатура, отправляем через answer
            sent_message = await message.answer(norm["text"], parse_mode=norm.get("parse_mode"), disable_web_page_preview=True, reply_markup=markup)
        else:
            # Если нет клавиатуры, используем send_long_message
            await send_long_message(message, norm["text"], parse_mode=norm.get("parse_mode"), reply_markup=markup)
            # Для send_long_message context нужно сохранять иначе, так как оно отправляет несколько сообщений
            # В этом случае context будет доступен только для первого сообщения
            return
        
        # Сохраняем context в отправленное сообщение
        if "context" in norm:
            sent_message.context = norm["context"]
    # Этот блок сработает, если есть только кнопки без текста
    elif markup:
         sent_message = await message.answer("Выберите вариант:", reply_markup=markup)
         if "context" in norm:
             sent_message.context = norm["context"]