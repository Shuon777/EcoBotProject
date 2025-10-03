# --- НАЧАЛО ФАЙЛА: handlers/gigachat_handler.py ---

import logging
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импортируем наши модули
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.api_handlers import handle_intent
from utils.bot_utils import send_long_message

logger = logging.getLogger(__name__)

class GigaChatHandler:
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager):
        """
        Инициализирует обработчик для GigaChat-режима.
        :param qa: Экземпляр QueryAnalyzer для анализа запросов.
        :param dialogue_manager: Экземпляр DialogueManager для управления контекстом.
        """
        self.qa = qa
        self.dialogue_manager = dialogue_manager

    async def process_message(self, message: types.Message):
        """
        Полная обработка сообщения пользователя в режиме GigaChat.
        """
        user_id = str(message.chat.id)
        query = message.text

        if not self.qa or not self.dialogue_manager.context_manager.redis_client:
            await message.answer("Извините, ключевые сервисы для GigaChat-режима недоступны.")
            return
        
        await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
        # Шаг 1: Анализ
        intent = await self.qa.detect_intent(query)
        entities_response = await self.qa.extract_entities(query, intent)
        if not entities_response.get("success"):
            await message.answer(f"Произошла ошибка при анализе вашего запроса: {entities_response.get('error', 'неизвестно')}")
            return
        current_entities = entities_response["result"]

        # Шаг 2: Получение категории объекта для более умной логики
        # Сначала пытаемся найти объект в текущем запросе, если нет - берем из прошлого
        final_object_for_category = current_entities.get("object") or \
                                    (self.dialogue_manager.context_manager.get_context(user_id)
                                     .get("history", [{}])[0].get("object"))
        object_category = await self.qa.get_object_category(final_object_for_category) if final_object_for_category else None

        # Шаг 3: Обработка хода диалога с помощью DialogueManager
        final_intent, final_entities, comparison_pair = self.dialogue_manager.process_turn(
            user_id=user_id, query=query, intent=intent, 
            current_entities=current_entities, object_category=object_category
        )
        
        # Шаг 4: Проверка полноты данных после обогащения контекстом
        if not final_entities.get("object") and final_intent in ["get_picture", "get_text", "get_location", "get_intersection_object_on_map"]:
            await message.answer("Пожалуйста, уточните, о каком растении или животном идет речь?")
            return
        if not final_entities.get("geo_place") and final_intent in ["get_objects_in_polygon"]:
            await message.answer("Пожалуйста, уточните, о каком месте идет речь?")
            return

        # Шаг 5: Выполнение основного действия
        logger.info(f"[USER_ID: {user_id}] GigaChat: Выполняем '{final_intent}' с сущностями: {final_entities}")
        responses = await handle_intent(
            intent=final_intent, result=final_entities, user_id=user_id,
            original_query=query, debug_mode=False
        )
        
        # Шаг 6: Отправка основного ответа
        for resp_data in responses:
            if resp_data.get("type") == "text":
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            elif resp_data.get("type") == "image":
                await message.answer_photo(resp_data["content"])
            elif resp_data.get("type") == "map":
                kb = InlineKeyboardMarkup().add(types.InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                if "caption" in resp_data and len(resp_data["caption"]) > 1024:
                    await message.answer_photo(photo=resp_data["static"], reply_markup=kb)
                    await send_long_message(message, resp_data["caption"])
                else:
                    await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", "Карта"), reply_markup=kb)

        # Шаг 7: Отправка предложения о сравнении, если оно было найдено
        if comparison_pair:
            obj1 = comparison_pair['object1']
            obj2 = comparison_pair['object2']
            text = f"Кстати, вы только что интересовались '{obj1}'. Хотите, я сравню для вас '{obj2}' и '{obj1}' по ключевым отличиям?"
            kb = InlineKeyboardMarkup().add(types.InlineKeyboardButton("Да, сравни!", callback_data="compare_objects"))
            await message.answer(text, reply_markup=kb)

    async def process_callback(self, callback_query: types.CallbackQuery):
        """
        Обрабатывает нажатия на инлайн-кнопки, специфичные для GigaChat-режима.
        """
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        message = callback_query.message

        # --- Логика для кнопки "Сравнить" ---
        if data == 'compare_objects':
            await callback_query.answer("Готовлю сравнение...")
            await message.edit_reply_markup(reply_markup=None)

            user_context = self.dialogue_manager.context_manager.get_context(user_id)
            history = user_context.get("history", [])

            if len(history) < 2:
                await message.answer("Извините, я потерял контекст для сравнения.")
                return

            object2 = history[0].get("object")
            object1 = history[1].get("object")

            if not object1 or not object2:
                await message.answer("Ошибка в данных контекста, не могу найти объекты для сравнения.")
                return

            responses = await handle_intent(
                intent="get_comparison", 
                result={"object1": object1, "object2": object2},
                user_id=user_id, original_query="", debug_mode=False
            )
            for resp_data in responses:
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            
            # Обновляем контекст, оставляя в истории только последний объект
            self.dialogue_manager.context_manager.set_context(user_id, {"history": [history[0]]})
            logger.info(f"[USER_ID: {user_id}] Контекст после сравнения обновлен. Последний объект: {object2}")
            return

        # --- Логика для кнопок уточнения (когда мы ее добавим) ---
        if data.startswith('clarify_object:'):
            await callback_query.answer("Эта функция в разработке.")
            return