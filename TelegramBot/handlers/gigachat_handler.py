# --- НАЧАЛО ФАЙЛА: handlers/gigachat_handler.py ---
import base64
import json
import logging
import aiohttp
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict, Any

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.api_handlers import handle_intent
from utils.bot_utils import send_long_message
from config import API_URLS

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

            final_entities["user_id"] = user_id

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
            selected_object = selected_object.replace('_', ' ').replace('-', ':')
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
        
        if data.startswith('fallback:'):
            await self.handle_fallback_callback(callback_query)
        
        elif data.startswith('explore:'):
            await self.handle_exploration_callback(callback_query)

    async def handle_fallback_callback(self, callback_query: types.CallbackQuery):
        """
        Обрабатывает выбор упрощения от пользователя
        """
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        message = callback_query.message
        
        try:
            parts = data.split(':', 2)
            fallback_type = parts[1]
            object_nom = parts[2]
            
            # Получаем исходные features из Redis
            from utils.context_manager import RedisContextManager
            context_manager = RedisContextManager()
            fallback_key = f"fallback_features:{user_id}"
            original_features = await context_manager.get_context(fallback_key)
            
            if not original_features:
                logger.error(f"Не найдены fallback features для {user_id}")
                await callback_query.answer("Ошибка: контекст утерян, попробуйте начать поиск заново")
                return
            
            logger.info(f"Восстановлены features из Redis для {user_id}: {original_features}")
            
            # Создаем упрощенные features на основе типа
            features = original_features.copy()
            
            if fallback_type == "no_season":
                features.pop("season", None)
                logger.info(f"Упрощение: без сезона, оставляем {features}")
            elif fallback_type == "no_habitat":
                features.pop("habitat", None)
                logger.info(f"Упрощение: без места, оставляем {features}")
            elif fallback_type == "basic":
                features = {}
                logger.info(f"Упрощение: только объект")
            
            # Удаляем fallback features из Redis после использования
            await context_manager.delete_context(fallback_key)
            
            # Убираем кнопки упрощения
            await message.edit_reply_markup(reply_markup=None)
            await callback_query.answer("Ищу упрощенный вариант...")
            
            # Создаем описание для нового запроса
            feature_desc = []
            if features.get("season"):
                feature_desc.append(features['season'])
            if features.get("habitat"):
                feature_desc.append(f"в {features['habitat']}")
                
            features_text = " ".join(feature_desc)
            simulated_query = f"Покажи {object_nom} {features_text}".strip()
            
            logger.info(f"Выполняем упрощенный запрос: {object_nom} с features: {features}")
            
            # Отправляем действие "печатает"
            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
            # Выполняем поиск с упрощенными параметрами
            responses = await handle_intent(
                self.session, "get_picture", {"object": object_nom, "features": features},
                user_id, simulated_query, False
            )
            
            # === ИСПРАВЛЕНИЕ: Сохраняем УПРОЩЕННЫЙ вариант в основной контекст ===
            # Создаем сущности для сохранения в историю
            context_entities = {"object": object_nom}
            if features:  # Добавляем features только если они есть
                context_entities["features"] = features
            
            # Определяем категорию объекта
            object_category = await self.qa.get_object_category(object_nom)
            
            # Сохраняем в основной контекст
            await self.dialogue_manager.update_history(
                user_id, "get_picture", context_entities, object_category
            )
            
            logger.info(f"Сохранили упрощенный контекст в историю: {context_entities}")
            
            # Отправляем результаты
            for resp in responses:
                if resp["type"] == "text":
                    await send_long_message(message, resp["content"])
                elif resp["type"] == "image":
                    await message.answer_photo(resp["content"])
                elif resp["type"] == "clarification":
                    kb = InlineKeyboardMarkup()
                    for row in resp["buttons"]:
                        kb.row(*[InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]) for btn in row])
                    await message.answer(resp["content"], reply_markup=kb)
                    
        except Exception as e:
            logger.error(f"Ошибка обработки fallback callback: {e}", exc_info=True)
            await message.answer("Произошла ошибка при упрощении запроса")
            await callback_query.answer()

    async def handle_exploration_callback(self, callback_query: types.CallbackQuery):
        """
        Обрабатывает выбор сценария исследования локации
        """
        data = callback_query.data
        message = callback_query.message
        user_id = str(callback_query.from_user.id)
        
        try:
            parts = data.split(':', 3)
            action = parts[1]  # overview, full_list, object
            
            if action == "object":
                # Обработка выбора конкретного объекта по индексу
                object_index = int(parts[2])
                geo_place = parts[3]
                await self.show_object_by_index(message, geo_place, object_index)
                return
                
            geo_place = parts[2]
            
            await message.edit_reply_markup(reply_markup=None)
            await callback_query.answer("Формирую обзор...")
            
            # Получаем список объектов
            url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
            payload = {"name": geo_place, "buffer_radius_km": 5}
            
            async with self.session.post(url, json=payload) as resp:
                if not resp.ok:
                    await message.answer("Ошибка при получении данных о локации")
                    return
                    
                data = await resp.json()
                objects_list = data.get("all_biological_names", [])
                
                if action == "overview":
                    await self.show_overview(message, geo_place, objects_list)
                elif action == "full_list":
                    await self.show_full_list(message, geo_place, objects_list)
                    
        except Exception as e:
            logger.error(f"Ошибка обработки exploration callback: {e}", exc_info=True)
            await message.answer("Произошла ошибка при формировании обзора")

    async def show_object_by_index(self, message: types.Message, geo_place: str, object_index: int):
        """
        Показывает информацию об объекте по его индексу в списке И ОБНОВЛЯЕТ КОНТЕКСТ
        """
        # Снова получаем список объектов
        url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
        payload = {"name": geo_place, "buffer_radius_km": 5}
        
        async with self.session.post(url, json=payload) as resp:
            if not resp.ok:
                await message.answer("Ошибка при получении данных")
                return
                
            data = await resp.json()
            objects_list = data.get("all_biological_names", [])
            
            if object_index >= len(objects_list):
                await message.answer("Объект не найден")
                return
                
            object_nom = objects_list[object_index]
            
            # === ВАЖНО: Сохраняем в контекст выбранный объект ===
            user_id = str(message.chat.id)
            context_entities = {"object": object_nom}
            
            # Определяем категорию объекта
            object_category = await self.qa.get_object_category(object_nom)
            
            # Сохраняем в основной контекст
            await self.dialogue_manager.update_history(
                user_id, "get_text", context_entities, object_category
            )
            
            logger.info(f"Обновили контекст для {user_id}: {context_entities}")
            
            # Используем существующий механизм уточнения
            simulated_text = f"Расскажи про {object_nom}"
            final_intent = "get_text"
            final_entities = {"object": object_nom}

            await message.bot.send_chat_action(chat_id=message.chat.id, action=types.ChatActions.TYPING)
            responses = await handle_intent(
                self.session, final_intent, final_entities, 
                user_id, simulated_text, False
            )
            
            for resp_data in responses:
                if resp_data.get("type") == "text":
                    preface = f"🌿 **{object_nom}** (из {geo_place})\n\n"
                    final_text = preface + resp_data["content"]
                    await send_long_message(message, final_text, parse_mode="Markdown")
                elif resp_data.get("type") == "image":
                    await message.answer_photo(resp_data["content"])
    
    async def show_full_list(self, message: types.Message, geo_place: str, objects_list: list):
        """
        Показывает полный список объектов (как было изначально)
        """
        if not objects_list:
            await message.answer(f"В районе '{geo_place}' не найдено объектов")
            return
        
        # Формируем красивый список
        objects_text = "• " + "\n• ".join(objects_list)
        
        text = f"📋 **Все объекты в районе {geo_place}**\n\n{objects_text}"
        
        # Предлагаем изучить конкретные объекты
        keyboard = InlineKeyboardMarkup()
        
        # Берем первые 3 объекта для быстрого доступа
        for i, obj in enumerate(objects_list[:3]):
            keyboard.add(InlineKeyboardButton(
                text=f"🌿 {obj}", 
                callback_data=f"explore:object:{i}:{geo_place}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="⬅️ Назад к обзору", 
            callback_data=f"explore:overview:{geo_place}"
        ))
    
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")

    async def show_overview(self, message: types.Message, geo_place: str, objects_list: list):
        """
        Показывает умный обзор через LLM с объяснениями
        """
        try:
            logger.info(f"Начинаем LLM анализ для {geo_place}, объектов: {len(objects_list)}")
            
            # Получаем анализ от LLM
            analysis = await self.analyze_location_objects(geo_place, objects_list)
            logger.info(f"LLM анализ успешен: {analysis}")
            
            text = f"🌿 **{geo_place}**\n\n"
            text += f"{analysis['statistics']}\n\n"
            
            # Показываем интересные объекты С ОБЪЯСНЕНИЯМИ
            if analysis.get('interesting_objects'):
                text += "🎯 **Самые интересные:**\n"
                for item in analysis['interesting_objects']:
                    text += f"• **{item['name']}** - {item['reason']}\n"
            
            # Кнопки для изучения
            keyboard = InlineKeyboardMarkup()
            
            if analysis.get('interesting_objects'):
                for item in analysis['interesting_objects'][:3]:
                    if item['name'] in objects_list:
                        idx = objects_list.index(item['name'])
                        keyboard.add(InlineKeyboardButton(
                            text=f"🔍 {item['name']}", 
                            callback_data=f"explore:object:{idx}:{geo_place}"
                        ))
            
            keyboard.add(InlineKeyboardButton(
                text="📋 Все объекты", 
                callback_data=f"explore:full_list:{geo_place}"
            ))
            
            await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
            
        except Exception as e:
            logger.error(f"Ошибка анализа локации: {e}", exc_info=True)
            # Fallback - простой список
            logger.info("Используем fallback - простой список")
            await self.show_simple_overview(message, geo_place, objects_list)
    
    async def analyze_location_objects(self, geo_place: str, objects_list: list) -> dict:
        """
        Анализирует список объектов локации через GigaChat
        """
        try:
            # Используем метод из QueryAnalyzer который уже есть
            return await self.qa.analyze_location_objects(geo_place, objects_list)
            
        except Exception as e:
            logger.error(f"Ошибка анализа локации через LLM: {e}")
            # Fallback - простая статистика
            return {
                "statistics": f"В локации {geo_place} найдено {len(objects_list)} биологических объектов.",
                "interesting_objects": objects_list[:3]  # Первые 3 как интересные
            }

    async def show_simple_overview(self, message: types.Message, geo_place: str, objects_list: list):
        """
        Простой обзор без LLM (fallback)
        """
        text = f"🌿 **{geo_place}**\n\n"
        text += f"Найдено объектов: {len(objects_list)}\n\n"
        text += "• " + "\n• ".join(objects_list[:8])
        
        if len(objects_list) > 8:
            text += f"\n\n... и еще {len(objects_list) - 8} объектов"
        
        keyboard = InlineKeyboardMarkup()
        for i, obj in enumerate(objects_list[:3]):
            keyboard.add(InlineKeyboardButton(
                text=f"🔍 {obj}", 
                callback_data=f"explore:object:{i}:{geo_place}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="📋 Все объекты", 
            callback_data=f"explore:full_list:{geo_place}"
        ))
        
        await message.answer(text, reply_markup=keyboard, parse_mode="Markdown")
