import logging
import aiohttp
from typing import Dict, Any, Callable, Awaitable
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.api_handlers import (
    handle_get_description, handle_get_picture, handle_draw_locate_map,
    handle_nearest, handle_objects_in_polygon, handle_geo_request, handle_comparison
)
from utils.bot_utils import send_long_message
from utils.context_manager import RedisContextManager
from config import API_URLS

logger = logging.getLogger(__name__)

# Типизация для наших словарей-диспетчеров, чтобы код был понятнее
ActionHandler = Callable[[Dict[str, Any], str, str], Awaitable[list]]
CallbackHandler = Callable[[types.CallbackQuery], Awaitable[None]]

class GigaChatHandler:
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session: aiohttp.ClientSession):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session

        # --- ДИСПЕТЧЕР ДЛЯ ТЕКСТОВЫХ СООБЩЕНИЙ ---
        # Ключ: (action, entity_type). Значение: функция-обработчик из api_handlers.
        self.action_handlers: Dict[tuple[str, str], ActionHandler] = {
            ("describe", "Biological"): handle_get_description,
            ("describe", "Infrastructure"): handle_geo_request,
            ("describe", "GeoPlace"): handle_geo_request,
            ("show_image", "Biological"): handle_get_picture,
            ("show_map", "Biological"): handle_draw_locate_map,
            ("find_nearby", "ANY"): handle_nearest, # ANY - для случаев, где тип не важен
            ("list_items", "Biological"): handle_objects_in_polygon,
            ("list_items", "Infrastructure"): handle_geo_request,
        }

        # --- ДИСПЕТЧЕР ДЛЯ ОБРАБОТКИ КНОПОК ---
        # Ключ: префикс callback_data. Значение: приватный метод этого класса.
        self.callback_handlers: Dict[str, CallbackHandler] = {
            "clarify_object": self._handle_clarify_object,
            "clarify_more": self._handle_pagination,
            "explore": self._handle_exploration,
            "fallback": self._handle_fallback,
        }

    async def process_message(self, message: types.Message):
        """Главный обработчик текстовых сообщений. Анализирует, обогащает и диспетчеризует."""
        user_id, query = str(message.chat.id), message.text
        
        try:
            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
            analysis = await self.qa.analyze_query(query)
            if not analysis:
                await self._reply_with_error(message, f"QueryAnalyzer не вернул анализ для запроса: '{query}'")
                return

            final_analysis = await self.dialogue_manager.enrich_request(user_id, analysis)
            action = final_analysis.get("action")

            if not action or action == "unknown":
                await self._reply_with_error(message, f"Итоговый action='unknown'. Анализ: {final_analysis}", reply_text="Пожалуйста, уточните ваш запрос.")
                return

            logger.info(f"[{user_id}] Роутинг для анализа: {final_analysis}")
            
            primary_entity_type = final_analysis.get("primary_entity", {}).get("type", "ANY")
            handler = self.action_handlers.get((action, primary_entity_type))
            
            # [ИЗМЕНЕНИЕ] - Добавляем обработку count_items, привязывая ее к существующему хэндлеру
            if not handler:
                if action == "count_items" and primary_entity_type == "Infrastructure":
                    handler = handle_geo_request
                else:
                    handler = self.action_handlers.get((action, "ANY"))

            if not handler:
                await self._reply_with_error(message, f"Для анализа {final_analysis} не найдено обработчика.", reply_text="Извините, я пока не умею обрабатывать такие запросы.")
                return

            logger.debug(f"[{user_id}] Диспетчер вызвал обработчик: {handler.__name__}")

            all_possible_args = {
                "session": self.session, "analysis": final_analysis,
                "user_id": user_id, "original_query": query, "debug_mode": False
            }
            import inspect
            handler_signature = inspect.signature(handler)
            required_args = handler_signature.parameters.keys()
            args_to_pass = {key: value for key, value in all_possible_args.items() if key in required_args}
            
            responses = await handler(**args_to_pass)
            
            was_successful = await self._send_responses(message, responses)
            if was_successful:
                await self.dialogue_manager.update_history(user_id, final_analysis)
            
        except Exception as e:
            logger.error(f"[{user_id}] КРИТИЧЕСКАЯ ОШИБКА в GigaChatHandler.process_message: {e}", exc_info=True)
            await message.answer("Ой, что-то пошло не так на моей стороне.")
            
    async def process_callback(self, callback_query: types.CallbackQuery):
        """Главный обработчик кнопок. Находит нужный обработчик и передает ему управление."""
        user_id, data = str(callback_query.from_user.id), callback_query.data
        
        try:
            # --- [НОВОЕ] ЛОГИКА ДИСПЕТЧЕРИЗАЦИИ ---
            prefix = data.split(':', 1)[0]
            handler = self.callback_handlers.get(prefix)

            if handler:
                logger.info(f"[{user_id}] Диспетчер кнопок вызвал: {handler.__name__}")
                await handler(callback_query)
            else:
                logger.warning(f"[{user_id}] Получен необработанный callback с префиксом '{prefix}': '{data}'")
                await callback_query.answer("Это действие больше не поддерживается.", show_alert=True)

        except Exception as e:
            logger.error(f"[{user_id}] Критическая ошибка в `process_callback` для data='{data}': {e}", exc_info=True)
            await callback_query.message.answer("Произошла ошибка при обработке вашего выбора.")
            await callback_query.answer()

    # --- Приватные методы для чистоты кода ---

    async def _send_responses(self, message: types.Message, responses: list) -> bool:
        """Отправляет отформатированные ответы пользователю."""
        was_successful = True
        for resp_data in responses:
            response_type = resp_data.get("type")
            if response_type in ["clarification", "clarification_map"]:
                was_successful = False
                keyboard = self._build_keyboard(resp_data.get("buttons"))
                if response_type == "clarification_map":
                    await message.answer_photo(photo=resp_data["static_map"], caption=resp_data["content"], reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await message.answer(resp_data["content"], reply_markup=keyboard, parse_mode="Markdown")
                break # После clarification другие сообщения не шлем
            elif response_type == "text":
                await send_long_message(message, resp_data["content"], parse_mode=resp_data.get("parse_mode"))
            elif response_type == "image":
                await message.answer_photo(resp_data["content"])
            elif response_type == "map":
                kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", ""), reply_markup=kb, parse_mode="Markdown")
        return was_successful

    def _build_keyboard(self, buttons_data: list) -> InlineKeyboardMarkup | None:
        """Универсальный сборщик инлайн-клавиатур."""
        if not buttons_data: return None
        kb = InlineKeyboardMarkup()
        for row in buttons_data:
            button_row = [InlineKeyboardButton(text=btn["text"], callback_data=btn.get("callback_data"), url=btn.get("url")) for btn in row]
            kb.row(*button_row)
        return kb

    async def _reply_with_error(self, message: types.Message, log_text: str, reply_text: str = "Произошла ошибка."):
        """Отправляет сообщение об ошибке и логирует ее."""
        logger.warning(f"[{message.chat.id}] {log_text}")
        await message.answer(reply_text)

    # --- Приватные обработчики для кнопок ---

    async def _handle_clarify_object(self, cq: types.CallbackQuery):
        await cq.message.edit_reply_markup(reply_markup=None)
        selected_object = cq.data.split(':', 1)[1]
        simulated_analysis = {"action": "describe", "primary_entity": {"name": selected_object, "type": "Biological"}}
        await self.dialogue_manager.update_history(cq.from_user.id, simulated_analysis)
        responses = await handle_get_description(self.session, simulated_analysis, cq.from_user.id, f"Уточнение: {selected_object}", False)
        await self._send_responses(cq.message, responses)
        await cq.answer()

    async def _handle_pagination(self, cq: types.CallbackQuery):
        await cq.answer("Ищу дальше...")
        _, ambiguous_term, offset_str = cq.data.split(':', 2)
        simulated_analysis = {"action": "describe", "primary_entity": {"name": ambiguous_term, "type": "Biological"}, "offset": int(offset_str)}
        responses = await handle_get_description(self.session, simulated_analysis, cq.from_user.id, f"Пагинация: {ambiguous_term}", False)
        # Для пагинации мы всегда должны редактировать сообщение
        for resp_data in responses:
            if resp_data.get("type") == "clarification":
                kb = self._build_keyboard(resp_data.get("buttons"))
                await cq.message.edit_text(resp_data["content"], reply_markup=kb)
            else:
                await cq.message.edit_text(resp_data.get("content", "Больше ничего не найдено."), reply_markup=None)

    async def _handle_exploration(self, cq: types.CallbackQuery):
        await cq.message.edit_reply_markup(reply_markup=None)
        _, action, geo_place = cq.data.split(':', 2)
        url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
        payload = {"name": geo_place, "buffer_radius_km": 5}
        async with self.session.post(url, json=payload) as resp:
            if not resp.ok:
                await cq.message.answer("Не удалось получить данные о локации.")
                return
            api_data = await resp.json()
            objects_list = api_data.get("all_biological_names", [])

        if not objects_list:
            await cq.message.answer(f"В районе «{geo_place}» не найдено объектов для обзора.")
            return

        if action == "full_list":
            text = f"📋 **Все объекты в районе «{geo_place}»**:\n\n" + "• " + "\n• ".join(objects_list)
            await send_long_message(cq.message, text, parse_mode="Markdown")
        
        elif action == "overview":
            await cq.message.answer("Минутку, готовлю умный обзор...")
            analysis = await self.qa.analyze_location_objects(geo_place, objects_list)
            text = f"🌿 **{geo_place}**\n\n{analysis['statistics']}\n\n"
            if analysis.get('interesting_objects'):
                text += "🎯 **Самые интересные:**\n"
                for item in analysis['interesting_objects']:
                    text += f"• **{item['name']}** - {item['reason']}\n"
            await send_long_message(cq.message, text, parse_mode="Markdown")
        await cq.answer()
    
    async def _handle_fallback(self, cq: types.CallbackQuery):
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer("Ищу упрощенный вариант...")
        
        user_id = str(cq.from_user.id)
        _, fallback_type, object_nom = cq.data.split(':', 2)
        logger.info(f"[{user_id}] Пользователь выбрал fallback: тип='{fallback_type}', объект='{object_nom}'")

        # 1. Получаем исходные атрибуты из Redis
        context_manager = RedisContextManager()
        fallback_key = f"fallback_attributes:{user_id}"
        original_attributes = await context_manager.get_context(fallback_key)
        
        if not original_attributes:
            await self._reply_with_error(cq.message, f"Не найдены атрибуты для fallback в Redis (key: {fallback_key})", "Ошибка: контекст для упрощения запроса утерян. Попробуйте снова.")
            return

        # 2. Создаем новый, упрощенный `analysis`
        simplified_attributes = original_attributes.copy()
        if fallback_type == "no_season":
            simplified_attributes.pop("season", None)
        elif fallback_type == "no_habitat":
            simplified_attributes.pop("habitat", None)
        elif fallback_type == "basic":
            simplified_attributes = {}

        simplified_analysis = {
            "action": "show_image",
            "primary_entity": {"name": object_nom, "type": "Biological"},
            "attributes": simplified_attributes,
        }
        
        # 3. Очищаем Redis
        await context_manager.delete_context(fallback_key)

        # 4. Вызываем `handle_get_picture` с новым анализом
        logger.debug(f"[{user_id}] Повторный вызов `handle_get_picture` с упрощенным анализом: {simplified_analysis}")
        responses = await handle_get_picture(self.session, simplified_analysis, user_id, False)

        # 5. Сохраняем успешный упрощенный запрос в основную историю
        await self.dialogue_manager.update_history(user_id, simplified_analysis)

        # 6. Отправляем результат
        await self._send_responses(cq.message, responses)