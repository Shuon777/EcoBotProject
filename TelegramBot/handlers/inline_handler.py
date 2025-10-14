# Файл: TelegramBot/handlers/inline_handler.py

import logging
from aiogram import types, Dispatcher
from uuid import uuid4

# Импортируем нашу функцию поиска
from logic.inline_search import find_suggestions

logger = logging.getLogger(__name__)

async def process_inline_query(inline_query: types.InlineQuery):
    """
    Обрабатывает входящие инлайн-запросы от пользователя.
    """
    # Получаем текст, который вводит пользователь
    query_text = inline_query.query
    
    # Ищем совпадения с помощью нашего "движка"
    found_items = find_suggestions(query_text)
    
    results = []
    for item_name in found_items:
        # Для каждого найденного объекта мы создаем ТРИ разные подсказки,
        # чтобы пользователь мог сразу выбрать, что он хочет сделать.
        
        # 1. Подсказка для получения ОПИСАНИЯ
        results.append(types.InlineQueryResultArticle(
            id=str(uuid4()), # Уникальный ID для каждой подсказки
            title=f"📖 {item_name}",
            description="Узнать описание и факты",
            # Это сообщение будет отправлено, когда пользователь нажмет на подсказку
            input_message_content=types.InputTextMessageContent(
                message_text=f"Расскажи про {item_name}"
            )
        ))
        
        # 2. Подсказка для получения ФОТО
        results.append(types.InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"🖼️ {item_name}",
            description="Посмотреть, как выглядит",
            input_message_content=types.InputTextMessageContent(
                message_text=f"Как выглядит {item_name}"
            )
        ))
        
        # 3. Подсказка для поиска на КАРТЕ
        results.append(types.InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"🗺️ {item_name}",
            description="Найти ареал обитания на карте",
            input_message_content=types.InputTextMessageContent(
                message_text=f"Где растет {item_name}"
            )
        ))

    # Отправляем сформированный список подсказок в Telegram.
    # cache_time=1 говорит Telegram не кэшировать результаты, чтобы они всегда были свежими.
    await inline_query.bot.answer_inline_query(inline_query.id, results=results, cache_time=1)

def register_inline_handlers(dp: Dispatcher):
    """Регистрирует обработчики для инлайн-режима."""
    dp.register_inline_handler(process_inline_query)