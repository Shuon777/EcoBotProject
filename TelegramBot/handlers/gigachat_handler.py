# --- НАЧАЛО ФАЙЛА: handlers/gigachat_handler.py ---

import logging
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.api_handlers import handle_intent
from utils.bot_utils import send_long_message

logger = logging.getLogger(__name__)

class GigaChatHandler:
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session

    async def process_message(self, message: types.Message):
        user_id = str(message.chat.id)
        query = message.text
        
        try:
            logger.info(f"--- [DEBUG START] process_message started for query: '{query}' ---")

            if not self.qa or not self.dialogue_manager.context_manager.redis_client:
                logger.error("[DEBUG FAIL] Critical services (QA or Redis) are not available.")
                await message.answer("Извините, ключевые сервисы для GigaChat-режима недоступны.")
                return
            
            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
            logger.info("[DEBUG STEP 1] Calling detect_intent...")
            intent = await self.qa.detect_intent(query)
            logger.info(f"[DEBUG STEP 2] detect_intent DONE. Result: {intent}")

            logger.info("[DEBUG STEP 3] Calling extract_entities...")
            entities_response = await self.qa.extract_entities(query, intent)
            logger.info(f"[DEBUG STEP 4] extract_entities DONE. Success: {entities_response.get('success')}")
            
            if not entities_response.get("success"):
                await message.answer(f"Произошла ошибка при анализе: {entities_response.get('error', 'неизвестно')}")
                return
            current_entities = entities_response["result"]

            logger.info("[DEBUG STEP 5] Getting object to classify...")
            object_to_classify = current_entities.get("object")
            if not object_to_classify:
                logger.info("[DEBUG STEP 6] No object in current query, getting context...")
                user_context = await self.dialogue_manager.context_manager.get_context(user_id)
                logger.info("[DEBUG STEP 7] get_context DONE.")
                history = user_context.get("history", [])
                if history:
                    object_to_classify = history[0].get("object")
            
            logger.info(f"[DEBUG STEP 8] Object to classify is: {object_to_classify}")

            logger.info("[DEBUG STEP 9] Calling get_object_category...")
            object_category = await self.qa.get_object_category(object_to_classify) if object_to_classify else None
            logger.info(f"[DEBUG STEP 10] get_object_category DONE. Result: {object_category}")

            logger.info("[DEBUG STEP 11] Calling dialogue_manager.process_turn...")
            final_intent, final_entities, comparison_pair = await self.dialogue_manager.process_turn(
                user_id=user_id, query=query, intent=intent, 
                current_entities=current_entities, object_category=object_category
            )
            logger.info("[DEBUG STEP 12] dialogue_manager.process_turn DONE.")

            if not final_entities.get("object") and final_intent in ["get_picture", "get_text", "get_location", "get_intersection_object_on_map"]:
                await message.answer("Пожалуйста, уточните, о каком растении или животном идет речь?")
                return
            if not final_entities.get("geo_place") and final_intent in ["get_objects_in_polygon"]:
                await message.answer("Пожалуйста, уточните, о каком месте идет речь?")
                return

            logger.info(f"[DEBUG STEP 13] Calling handle_intent for intent '{final_intent}'...")
            responses = await handle_intent(
                session=self.session, intent=final_intent, result=final_entities, 
                user_id=user_id, original_query=query, debug_mode=False
            )
            logger.info("[DEBUG STEP 14] handle_intent DONE.")

            for i, resp_data in enumerate(responses):
                logger.info(f"[DEBUG STEP 15.{i}] Processing response of type: {resp_data.get('type')}")
                if resp_data.get("type") == "text":
                    await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
                elif resp_data.get("type") == "image":
                    await message.answer_photo(resp_data["content"])
                elif resp_data.get("type") == "map":
                    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                    if "caption" in resp_data and len(resp_data["caption"]) > 1024:
                        await message.answer_photo(photo=resp_data["static"], reply_markup=kb)
                        await send_long_message(message, resp_data["caption"])
                    else:
                        await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", "Карта"), reply_markup=kb)

            logger.info("[DEBUG STEP 16] All responses sent.")

            if comparison_pair:
                logger.info("[DEBUG STEP 17] Comparison pair found, sending button...")
                obj1 = comparison_pair['object1']
                obj2 = comparison_pair['object2']
                text = f"Кстати, вы только что интересовались '{obj1}'. Хотите, я сравню для вас '{obj2}' и '{obj1}' по ключевым отличиям?"
                kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Да, сравни!", callback_data="compare_objects"))
                await message.answer(text, reply_markup=kb)
            
            logger.info("--- [DEBUG END] process_message finished successfully. ---")

        except Exception as e:
            logger.error(f"!!! КРИТИЧЕСКАЯ ОШИБКА в GigaChatHandler.process_message: {e}", exc_info=True)
            await message.answer("Ой, что-то пошло не так на моей стороне. Попробуйте еще раз.")

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

            user_context = await self.dialogue_manager.context_manager.get_context(user_id)
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
                session=self.session,
                intent="get_comparison", 
                result={"object1": object1, "object2": object2},
                user_id=user_id, original_query="", debug_mode=False
            )
            for resp_data in responses:
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            
            await self.dialogue_manager.context_manager.set_context(user_id, {"history": [history[0]]})
            logger.info(f"[USER_ID: {user_id}] Контекст после сравнения обновлен. Последний объект: {object2}")
            return

        # --- Логика для кнопок уточнения ---
        if data.startswith('clarify_object:'):
            await callback_query.answer("Эта функция в разработке.")
            return

# --- КОНЕЦ ФАЙЛА: handlers/gigachat_handler.py ---