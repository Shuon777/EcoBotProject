import logging
import aiohttp
from typing import Dict, Any, Callable, Awaitable
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import re
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.action_handlers.biological import handle_get_description, handle_get_picture
from logic.action_handlers.geospatial import (
    handle_draw_locate_map, handle_nearest, handle_objects_in_polygon,
    handle_geo_request, handle_draw_map_of_infrastructure
)
from utils.bot_utils import send_long_message
from utils.context_manager import RedisContextManager
from config import API_URLS

unhandled_logger = logging.getLogger("unhandled")
logger = logging.getLogger(__name__)

ActionHandler = Callable[[Dict[str, Any], str, str], Awaitable[list]]
CallbackHandler = Callable[[types.CallbackQuery], Awaitable[None]]

class FakeCallbackQuery:
    def __init__(self, message: types.Message, data: str):
        self.message = message
        self.data = data
        self.from_user = message.from_user
    async def answer(self, *args, **kwargs):
        pass

class GigaChatHandler:
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session: aiohttp.ClientSession):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session
        self.action_handlers: Dict[tuple[str, str], ActionHandler] = {
            ("describe", "Biological"): handle_get_description,
            ("describe", "Infrastructure"): handle_geo_request,
            ("describe", "GeoPlace"): handle_geo_request,
            ("show_image", "Biological"): handle_get_picture,
            ("show_map", "Biological"): handle_draw_locate_map,
            ("show_map", "Infrastructure"): handle_draw_map_of_infrastructure,
            ("find_nearby", "ANY"): handle_nearest,
            ("list_items", "Biological"): handle_objects_in_polygon,
            ("list_items", "Infrastructure"): handle_geo_request,
        }

        self.callback_handlers: Dict[str, CallbackHandler] = {
            "clarify_idx": self._handle_clarify_by_index,
            "clarify_more": self._handle_pagination,
            "explore": self._handle_exploration,
            "fallback": self._handle_fallback,
        }

    def _clean_text_for_comparison(self, text: str) -> str:
        if not text:
            return ""
        # Оставляем только буквы (русские и латинские), цифры и пробелы
        cleaned_text = re.sub(r'[^a-zA-Zа-яА-Я0-9\s]', '', text)
        # Заменяем несколько пробелов на один и убираем по краям
        return ' '.join(cleaned_text.split()).lower()

    async def process_message(self, message: types.Message):
        """
        Главный обработчик текстовых сообщений. Анализирует, обогащает и диспетчеризует.
        Теперь включает логику для обработки текстовых команд, дублирующих кнопки.
        """
        user_id, query = str(message.chat.id), message.text
        
        try:
            # --- [НОВАЯ ЛОГИКА ПРОВЕРКИ ТЕКСТА НА КОМАНДУ-КНОПКУ] ---
            latest_history = await self.dialogue_manager.get_latest_history(user_id)
            if latest_history:
                # Ищем кнопки в ответе из последней записи истории
                last_response = latest_history.get("response", [])
                if last_response and last_response[0].get("buttons"):
                    buttons_data = last_response[0]["buttons"]
                    
                    # Проходим по всем кнопкам и ищем совпадение по тексту
                    for row in buttons_data:
                        for button in row:
                            clean_button_text = self._clean_text_for_comparison(button.get("text"))
                            clean_query = self._clean_text_for_comparison(query)
                            if clean_button_text and clean_button_text == clean_query:
                                logger.info(f"[{user_id}] Текстовая команда '{query}' распознана как кнопка.")
                                
                                callback_data = button.get("callback_data")
                                if not callback_data:
                                    continue # Пропускаем кнопки-ссылки (у них нет callback_data)

                                # Создаем "фальшивый" callback query
                                fake_cq = FakeCallbackQuery(message=message, data=callback_data)
                                
                                # Находим и вызываем нужный обработчик для этой кнопки
                                prefix = callback_data.split(':', 1)[0]
                                handler = self.callback_handlers.get(prefix)
                                
                                if handler:
                                    logger.info(f"[{user_id}] Вызываем обработчик {handler.__name__} для текстовой команды.")
                                    await handler(fake_cq)
                                    return # Важно! Прекращаем дальнейшую обработку.
            # --- [КОНЕЦ НОВОЙ ЛОГИКИ] ---

            # Если код дошел до сюда, значит, это обычное текстовое сообщение, а не команда-кнопка.
            # Запускаем стандартный процесс обработки.
            
            await message.bot.send_chat_action(chat_id=user_id, action=types.ChatActions.TYPING)
            
            # Анализируем текущий запрос (с учетом истории для LLM)
            analysis = await self.qa.analyze_query(query, history=latest_history)
            if not analysis:
                await self._reply_with_error(message, f"QueryAnalyzer не вернул анализ для запроса: '{query}'")
                return

            # Обогащаем результат анализа (для случаев "а осенью?")
            final_analysis = await self.dialogue_manager.enrich_request(user_id, analysis)
            
            action = final_analysis.get("action")
            handler = None
            if action and action != "unknown":
                primary_entity_type = final_analysis.get("primary_entity", {}).get("type", "ANY")
                handler = self.action_handlers.get((action, primary_entity_type))
                if not handler:
                    if action == "count_items" and primary_entity_type == "Infrastructure":
                        handler = handle_geo_request
                    else:
                        handler = self.action_handlers.get((action, "ANY"))

            if not handler:
                unhandled_logger.info(f"USER_ID [{user_id}] - QUERY: \"{query}\"")
                fallback_keyboard = types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(text="💡 Начать поиск с подсказками", switch_inline_query_current_chat="")
                )
                await message.answer(
                    "К сожалению, я не смог распознать ваш запрос. Попробуйте использовать быстрый поиск с автодополнением.",
                    reply_markup=fallback_keyboard
                )
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
            
            await self._send_responses(message, responses)
            
            # Сохраняем историю в Redis
            await self.dialogue_manager.update_history(user_id, query, final_analysis, responses)
            
        except Exception as e:
            logger.error(f"[{user_id}] КРИТИЧЕСКАЯ ОШИБКА в GigaChatHandler.process_message: {e}", exc_info=True)
            await message.answer("Ой, что-то пошло не так на моей стороне.")
            
    async def process_callback(self, callback_query: types.CallbackQuery):
        """Главный обработчик кнопок. Находит нужный обработчик и передает ему управление."""
        user_id, data = str(callback_query.from_user.id), callback_query.data
        
        try:
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

    async def _send_responses(self, message: types.Message, responses: list):
        """Отправляет отформатированные ответы пользователю."""
        for resp_data in responses:
            response_type = resp_data.get("type")
            if response_type in ["clarification", "clarification_map"]:
                keyboard = self._build_keyboard(resp_data.get("buttons"))
                if response_type == "clarification_map":
                    await message.answer_photo(photo=resp_data["static_map"], caption=resp_data["content"], reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await message.answer(resp_data["content"], reply_markup=keyboard, parse_mode="Markdown")
                break # После clarification другие сообщения не шлем
            elif response_type == "text":
                await send_long_message(message, resp_data["content"], parse_mode="Markdown")
            elif response_type == "image":
                await message.answer_photo(resp_data["content"])
            elif response_type == "map":
                logger.info(f"Пытаюсь отправить карту: {resp_data['static']}")
                kb = InlineKeyboardMarkup().add(InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"]))
                await message.answer_photo(photo=resp_data["static"], caption=resp_data.get("caption", ""), reply_markup=kb, parse_mode="Markdown")

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

    async def _handle_pagination(self, cq: types.CallbackQuery):
        """Обрабатывает кнопку "Поискать еще", получая данные из Redis."""
        await cq.answer("Ищу дальше...")
        user_id = str(cq.from_user.id)

        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        context_data = await context_manager.get_context(options_key)

        if not context_data:
            await cq.message.edit_text("Извините, этот поиск уже неактуален. Пожалуйста, повторите ваш запрос.")
            return

        ambiguous_term = context_data.get("original_term")
        current_offset = context_data.get("offset", 0)
        options_count = len(context_data.get("options", []))
        
        if not ambiguous_term:
            await cq.message.edit_text("Произошла ошибка: не удалось найти исходный запрос для продолжения поиска.")
            return

        new_offset = current_offset + options_count
        simulated_analysis = {
            "action": "describe",
            "primary_entity": {"name": ambiguous_term, "type": "Biological"},
            "offset": new_offset
        }

        responses = await handle_get_description(self.session, simulated_analysis, user_id, f"Пагинация: {ambiguous_term}", False)
        
        if responses and responses[0].get("type") == "clarification":
            resp_data = responses[0]
            kb = self._build_keyboard(resp_data.get("buttons"))
            await cq.message.edit_text(resp_data["content"], reply_markup=kb)
        else:
            final_text = "Больше ничего не найдено."
            if responses and responses[0].get("content"):
                final_text = responses[0].get("content")
            await cq.message.edit_text(final_text, reply_markup=None)

    async def _handle_exploration(self, cq: types.CallbackQuery):
        """
        Обрабатывает нажатие на кнопки "Умный обзор" или "Полный список".
        Может быть вызван как через настоящий CallbackQuery, так и через наш FakeCallbackQuery.
        """
        # [ИСПРАВЛЕНИЕ]
        # Проверяем, настоящий ли это CallbackQuery. Только у него есть атрибут `id`.
        # Наш FakeCallbackQuery его не имеет.
        is_real_callback = isinstance(cq, types.CallbackQuery)

        if is_real_callback:
            # Если это настоящее нажатие кнопки, подтверждаем его и убираем клавиатуру.
            await cq.answer("Загружаю данные...")
            await cq.message.edit_reply_markup(reply_markup=None)
        # Если это FakeCallbackQuery (вызов через текст), мы ничего из этого не делаем.
        
        user_id = str(cq.from_user.id)
        _, action, geo_place = cq.data.split(':', 2)
        
        url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
        payload = {"name": geo_place, "buffer_radius_km": 5}
        
        # Мы используем cq.message, которое есть и в настоящем, и в "фальшивом" объекте,
        # чтобы отправить ответ в нужный чат.
        message_to_reply = cq.message

        async with self.session.post(url, json=payload) as resp:
            if not resp.ok:
                await message_to_reply.answer("Не удалось получить данные о локации.")
                return
            api_data = await resp.json()
            objects_list = api_data.get("all_biological_names", [])

        if not objects_list:
            await message_to_reply.answer(f"В районе «{geo_place}» не найдено объектов для обзора.")
            return

        simulated_query = f"Пользователь выбрал '{action}' для локации '{geo_place}'"
        simulated_analysis = {"action": "list_items", "primary_entity": None, "secondary_entity": {"name": geo_place, "type": "GeoPlace"}}
        response_to_save = []
        
        if action == "full_list":
            text = f"📋 **Все объекты в районе «{geo_place}»**:\n\n" + "• " + "\n• ".join(objects_list)
            await send_long_message(message_to_reply, text, parse_mode="Markdown")
            response_to_save.append({"type": "text", "content": text})
        
        elif action == "overview":
            await message_to_reply.answer("Минутку, готовлю умный обзор...")
            analysis = await self.qa.analyze_location_objects(geo_place, objects_list)
            
            text = f"🌿 **{geo_place}**\n\n{analysis['statistics']}\n\n"
            if analysis.get('interesting_objects'):
                text += "🎯 **Самые интересные:**\n"
                for item in analysis['interesting_objects']:
                    text += f"• **{item['name']}** - {item['reason']}\n"
            await send_long_message(message_to_reply, text, parse_mode="Markdown")
            response_to_save.append({"type": "text", "content": text})

        if response_to_save:
            await self.dialogue_manager.update_history(user_id, simulated_query, simulated_analysis, response_to_save)
                
    async def _handle_fallback(self, cq: types.CallbackQuery):
        is_real_callback = isinstance(cq, types.CallbackQuery)
        if is_real_callback:
            await cq.message.edit_reply_markup(reply_markup=None)
            await cq.answer("Ищу упрощенный вариант...")
        
        user_id = str(cq.from_user.id)
        _, fallback_type, object_nom = cq.data.split(':', 2)
        logger.info(f"[{user_id}] Пользователь выбрал fallback: тип='{fallback_type}', объект='{object_nom}'")

        context_manager = RedisContextManager()
        fallback_key = f"fallback_attributes:{user_id}"
        original_attributes = await context_manager.get_context(fallback_key)
        
        if not original_attributes:
            await self._reply_with_error(cq.message, f"Не найдены атрибуты для fallback в Redis (key: {fallback_key})", "Ошибка: контекст для упрощения запроса утерян. Попробуйте снова.")
            return

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
        
        await context_manager.delete_context(fallback_key)

        logger.debug(f"[{user_id}] Повторный вызов `handle_get_picture` с упрощенным анализом: {simplified_analysis}")
        responses = await handle_get_picture(self.session, simplified_analysis, user_id, False)
        simulated_query = f"Упрощенный запрос (fallback): {object_nom}"
        await self.dialogue_manager.update_history(user_id, simulated_query, simplified_analysis, responses)

        await self._send_responses(cq.message, responses)
    
    
    async def _handle_clarify_by_index(self, cq: types.CallbackQuery):
        """Обрабатывает выбор уточнения по индексу из списка в Redis."""
        is_real_callback = isinstance(cq, types.CallbackQuery)
        if is_real_callback:
            await cq.message.edit_reply_markup(reply_markup=None)
        user_id = str(cq.from_user.id)
        
        try:
            selected_index = int(cq.data.split(':', 1)[1])
        except (ValueError, IndexError):
            await cq.answer("Ошибка в данных кнопки.", show_alert=True)
            return

        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        context_data = await context_manager.get_context(options_key)
        options = context_data.get("options", [])

        if not options or selected_index >= len(options):
            await cq.message.answer("Извините, этот выбор уже неактуален. Пожалуйста, повторите ваш запрос.")
            await cq.answer()
            return

        selected_object = options[selected_index]
        await cq.answer(f"Выбрано: {selected_object}")

        simulated_analysis = {"action": "describe", "primary_entity": {"name": selected_object, "type": "Biological"}}
        responses = await handle_get_description(self.session, simulated_analysis, user_id, f"Уточнение: {selected_object}", False)
        simulated_query = f"Выбор из уточнений: {selected_object}"
        await self.dialogue_manager.update_history(user_id, simulated_query, simulated_analysis, responses)
        await self._send_responses(cq.message, responses)
        await context_manager.delete_context(options_key)
