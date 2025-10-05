# --- НАЧАЛО ФАЙЛА: handlers/gigachat_handler.py ---

import logging
import aiohttp
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict, Any

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.api_handlers import handle_intent
from utils.bot_utils import send_long_message

logger = logging.getLogger(__name__)

# [НОВОЕ] Словарь обязательных сущностей и функция-валидатор
REQUIRED_ENTITIES = {
    "get_picture": ["object"],
    "get_text": ["object"],
    "get_location": ["object"],
    "get_objects_in_polygon": ["geo_place"],
    "get_intersection_object_on_map": ["object", "geo_place"],
}

ACTION_VERBS = ["расскажи", "покажи", "опиши", "выглядит", "где", "найти", "растет", "обитает", "встретить"]

def is_request_complete(intent: str, entities: Dict[str, Any]) -> bool:
    """Проверяет, содержит ли запрос все необходимые сущности для выполнения интента."""
    if intent not in REQUIRED_ENTITIES:
        return False
    return all(entities.get(entity) for entity in REQUIRED_ENTITIES[intent])


class GigaChatHandler:
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session: aiohttp.ClientSession):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session

    async def process_message(self, message: types.Message):
        user_id = str(message.chat.id)
        query = message.text
        
        try:
            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
            # --- Этап 1: Анализ запроса (NLU) ---
            intent = await self.qa.detect_intent(query)
            entities_response = await self.qa.extract_entities(query, intent)
            
            if not entities_response.get("success"):
                await message.answer(f"Произошла ошибка при анализе: {entities_response.get('error', 'неизвестно')}")
                return
            
            entities = entities_response["result"]
            
            # --- Этап 2: Проверка полноты с учетом неоднозначности ---
            
            # Эвристика для определения коротких, неявных запросов
            is_ambiguous_query = len(query.split()) <= 3 and not any(verb in query.lower() for verb in ACTION_VERBS)
            
            # Проверяем полноту запроса по стандартным правилам
            is_complete_by_rules = is_request_complete(intent, entities)

            # Если запрос формально полный, но при этом короткий и неявный,
            # мы не доверяем LLM и считаем его НЕПОЛНЫМ, чтобы использовать контекст.
            if is_complete_by_rules and is_ambiguous_query:
                logger.info(f"Запрос '{query}' ПОЛНЫЙ, но похож на уточнение. Принудительно считаем НЕПОЛНЫМ.")
                is_final_complete = False
            else:
                is_final_complete = is_complete_by_rules

            if is_final_complete:
                logger.info(f"Запрос ПОЛНЫЙ. Интент: {intent}, Сущности: {entities}")
                final_intent, final_entities = intent, entities
            else:
                logger.info(f"Запрос НЕПОЛНЫЙ. Интент: {intent}, Сущности: {entities}. Обращение к DM...")
                # Передаем сам запрос в DM, чтобы он тоже мог использовать эвристику
                final_intent, final_entities = await self.dialogue_manager.enrich_request(user_id, query, intent, entities)
                logger.info(f"DM обогатил запрос. Результат: Интент: {final_intent}, Сущности: {final_entities}")

            # --- Этап 3: Обновление контекста и проверка на сравнение (DM) ---
            object_to_classify = final_entities.get("object")
            object_category = await self.qa.get_object_category(object_to_classify) if object_to_classify else None
            
            comparison_pair = await self.dialogue_manager.update_and_check_comparison(
                user_id, final_intent, final_entities, object_category
            )
            
            # --- Этап 4: Финальная проверка и исполнение ---
            if not is_request_complete(final_intent, final_entities):
                await message.answer("Пожалуйста, уточните, о каком растении, животном или месте идет речь?")
                return

            responses = await handle_intent(
                session=self.session, intent=final_intent, result=final_entities, 
                user_id=user_id, original_query=query, debug_mode=False
            )

            # --- Этап 5: Отправка ответа пользователю ---
            for resp_data in responses:
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

            if comparison_pair:
                obj1 = comparison_pair['object1']
                obj2 = comparison_pair['object2']
                text = f"Кстати, вы только что интересовались '{obj1}'. Хотите, я сравню для вас '{obj2}' и '{obj1}' по ключевым отличиям?"
                kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Да, сравни!", callback_data="compare_objects"))
                await message.answer(text, reply_markup=kb)
            
        except Exception as e:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА в GigaChatHandler.process_message: {e}", exc_info=True)
            await message.answer("Ой, что-то пошло не так на моей стороне. Попробуйте еще раз.")

    async def process_callback(self, callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        message = callback_query.message

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
        if data.startswith('clarify_object:'):
            await callback_query.answer("Эта функция в разработке.")
            return

# --- КОНЕЦ ФАЙЛА: handlers/gigachat_handler.py ---