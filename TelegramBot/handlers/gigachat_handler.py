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

REQUIRED_ENTITIES = {
    "get_picture": ["object"],
    "get_text": ["object"],
    "get_location": ["object"],
    "get_objects_in_polygon": ["geo_place"],
    "get_intersection_object_on_map": ["object", "geo_place"],
}

ACTION_VERBS = ["расскажи", "покажи", "опиши", "выглядит", "где", "найти", "растет", "обитает", "встретить"]

def is_request_complete(intent: str, entities: Dict[str, Any]) -> bool:
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
            
            intent = await self.qa.detect_intent(query)
            entities_response = await self.qa.extract_entities(query, intent)
            
            if not entities_response.get("success"):
                await message.answer(f"Произошла ошибка при анализе: {entities_response.get('error', 'неизвестно')}")
                return
            
            entities = entities_response["result"]
            
            is_ambiguous_query = len(query.split()) <= 3 and not any(verb in query.lower() for verb in ACTION_VERBS)
            is_complete_by_rules = is_request_complete(intent, entities)

            if is_complete_by_rules and is_ambiguous_query:
                is_final_complete = False
            else:
                is_final_complete = is_complete_by_rules

            if is_final_complete:
                final_intent, final_entities = intent, entities
            else:
                final_intent, final_entities = await self.dialogue_manager.enrich_request(user_id, query, intent, entities)

            if not is_request_complete(final_intent, final_entities):
                await message.answer("Пожалуйста, уточните, о каком растении, животном или месте идет речь?")
                return

            responses = await handle_intent(
                session=self.session, intent=final_intent, result=final_entities, 
                user_id=user_id, original_query=query, debug_mode=False
            )
            
            was_successful = True
            resolved_canonical_name = None

            for resp_data in responses:
                if resp_data.get("type") == "clarification":
                    kb = InlineKeyboardMarkup()
                    for row in resp_data["buttons"]:
                        kb.row(*[InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]) for btn in row])
                    await message.answer(resp_data["content"], reply_markup=kb)
                    was_successful = False 
                    break 
                
                if resp_data.get("type") == "text":
                    final_text = resp_data["content"]
                    if resp_data.get("canonical_name"):
                        resolved_canonical_name = resp_data["canonical_name"]
                        original_object = final_entities.get("object")
                        
                        if resolved_canonical_name.lower() != original_object.lower():
                            preface = f"По вашему запросу '{original_object}' найдена информация о **'{resolved_canonical_name}'**:\n\n"
                            final_text = preface + final_text
                    
                    await send_long_message(message, final_text, parse_mode="Markdown")
                
                elif resp_data.get("type") == "image":
                    await message.answer_photo(resp_data["content"])
                elif resp_data.get("type") == "map":
                    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                    if "caption" in resp_data and len(resp_data["caption"]) > 1024:
                        await message.answer_photo(photo=resp_data["static"], reply_markup=kb)
                        await send_long_message(message, resp_data["caption"])
                    else:
                        await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", "Карта"), reply_markup=kb)

            if was_successful:
                if resolved_canonical_name:
                    final_entities['object'] = resolved_canonical_name

                object_to_classify = final_entities.get("object")
                object_category = await self.qa.get_object_category(object_to_classify) if object_to_classify else None
                
                comparison_pair = await self.dialogue_manager.get_comparison_pair(
                    user_id, final_intent, final_entities, object_category
                )
                
                await self.dialogue_manager.update_history(
                    user_id, final_intent, final_entities, object_category
                )
                
                if comparison_pair:
                    obj1 = comparison_pair['object1']
                    obj2 = comparison_pair['object2']
                    text = f"Кстати, вы только что интересовались '{obj1}'. Хотите, я сравню для вас '{obj2}' и '{obj1}' по ключевым отличиям?"
                    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Да, сравни!", callback_data="compare_objects"))
                    await message.answer(text, reply_markup=kb)
            
        except Exception as e:
            logger.error(f"КРИТИЧЕСКАЯ ОШИБКА в GigaChatHandler.process_message: {e}", exc_info=True)
            await message.answer("Ой, что-то пошло не так на моей стороне.")

    async def process_callback(self, callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        message = callback_query.message
        
        if data.startswith('clarify_object:'):
            await message.edit_reply_markup(reply_markup=None)
            
            selected_object = data.split(':', 1)[1]
            logger.info(f"Пользователь уточнил объект: '{selected_object}'")

            simulated_text = f"Расскажи про {selected_object}"
            final_intent = "get_text"
            final_entities = {"object": selected_object}

            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            responses = await handle_intent(self.session, final_intent, final_entities, user_id, simulated_text, False)
            
            was_successful = True
            for resp_data in responses:
                if resp_data.get("type") == "text":
                    preface = f"Отлично! Вот информация про **'{selected_object}'**:\n\n"
                    final_text = preface + resp_data["content"]
                    await send_long_message(message, final_text, parse_mode="Markdown")
                elif resp_data.get("type") == "image":
                    await message.answer_photo(resp_data["content"])

            if was_successful:
                object_category = await self.qa.get_object_category(selected_object)
                
                comparison_pair = await self.dialogue_manager.get_comparison_pair(
                    user_id, final_intent, final_entities, object_category
                )
                
                await self.dialogue_manager.update_history(
                    user_id, final_intent, final_entities, object_category
                )
                
                if comparison_pair:
                    obj1 = comparison_pair['object1']
                    obj2 = comparison_pair['object2']
                    text = f"Кстати, вы только что интересовались '{obj1}'. Хотите, я сравню для вас '{obj2}' и '{obj1}' по ключевым отличиям?"
                    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Да, сравни!", callback_data="compare_objects"))
                    await message.answer(text, reply_markup=kb)

            await callback_query.answer()
            return

        if data.startswith('clarify_more:'):
            # Эта логика теперь должна работать без Redis
            try:
                parts = data.split(':', 2)
                ambiguous_term = parts[1]
                offset = int(parts[2])
            except (IndexError, ValueError):
                await callback_query.answer("Ошибка в данных кнопки.", show_alert=True)
                return

            logger.info(f"Запрос на пагинацию: '{ambiguous_term}' со смещением {offset}")
            await callback_query.answer("Ищу дальше...")

            responses = await handle_intent(
                self.session, "get_text", {"object": ambiguous_term, "offset": offset},
                user_id, ambiguous_term, False
            )

            for resp_data in responses:
                if resp_data.get("type") == "clarification":
                    kb = InlineKeyboardMarkup()
                    for row in resp_data["buttons"]:
                        kb.row(*[InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]) for btn in row])
                    await message.edit_text(resp_data["content"], reply_markup=kb)
                    return
            
            await message.edit_text("Больше ничего не найдено.")
            return

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
                session=self.session, intent="get_comparison", 
                result={"object1": object1, "object2": object2},
                user_id=user_id, original_query="", debug_mode=False
            )
            for resp_data in responses:
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            
            await self.dialogue_manager.context_manager.set_context(user_id, {"history": [history[0]]})
            logger.info(f"[USER_ID: {user_id}] Контекст после сравнения обновлен. Последний объект: {object2}")
            return

# --- КОНЕЦ ФАЙЛА: handlers/gigachat_handler.py ---