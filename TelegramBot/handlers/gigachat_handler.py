import logging
import aiohttp
import inspect
from typing import Dict, Any, Callable, Awaitable, List
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import re

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager

# Импортируем чистые функции логики
from logic.action_handlers.biological import handle_get_description, handle_get_picture
from logic.action_handlers.geospatial import (
    handle_draw_locate_map, handle_nearest, handle_objects_in_polygon,
    handle_geo_request, handle_draw_map_of_infrastructure, handle_draw_map_of_list_stub
)

# Импортируем нашу модель данных
from core.model import CoreResponse

from utils.bot_utils import send_long_message, convert_llm_markdown_to_html
from utils.settings_manager import get_user_settings
from utils.context_manager import RedisContextManager
from utils.feedback_manager import FeedbackManager
from utils.error_logger import send_error_log
from config import API_URLS

unhandled_logger = logging.getLogger("unhandled")
logger = logging.getLogger(__name__)

# Тип для обработчика действий (теперь возвращает List[CoreResponse])
ActionHandler = Callable[..., Awaitable[List[CoreResponse]]]
CallbackHandler = Callable[[types.CallbackQuery], Awaitable[None]]

class FakeCallbackQuery:
    """Имитирует CallbackQuery для обработки текстовых команд как кнопок."""
    def __init__(self, message: types.Message, data: str):
        self.message = message
        self.data = data
        self.from_user = message.from_user
    
    async def answer(self, *args, **kwargs):
        pass


class GigaChatHandler:
    """
    АДАПТЕР TELEGRAM.
    Связывает входящие сообщения Telegram с чистой бизнес-логикой.
    """
    
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session: aiohttp.ClientSession):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session
        
        # Маппинг действий на чистые функции логики
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
            ("count_items", "Infrastructure"): handle_geo_request,
            ("get_help", "ANY"): self._handle_help_request,       # Локальный метод, возвращает CoreResponse
            ("small_talk", "ANY"): self._handle_small_talk_request, # Локальный метод
        }

        self.callback_handlers: Dict[str, CallbackHandler] = {
            "clarify_idx": self._handle_clarify_by_index,
            "clarify_more": self._handle_pagination,
            "explore": self._handle_exploration,
            "fallback": self._handle_fallback,
        }
    
    # --- Вспомогательные хендлеры (обертки в CoreResponse) ---

    async def _handle_help_request(self, original_query: str, **kwargs) -> List[CoreResponse]:
        answer_text = await self.qa.answer_general_question(original_query)
        return [CoreResponse(type="text", content=answer_text)]
    
    async def _handle_small_talk_request(self, original_query: str, **kwargs) -> List[CoreResponse]:
        answer = await self.qa.reply_to_small_talk(original_query)
        return [CoreResponse(type="text", content=answer)]
    
    async def _handle_unknown_entity_wrapper(self, analysis: dict, **kwargs) -> List[CoreResponse]:
        entity_name = analysis.get("primary_entity", {}).get("name", "этот объект")
        text = (
            f"🤔 Я изучаю Байкал, но про «{entity_name}» в контексте "
            f"флоры, фауны или достопримечательностей озера я ничего не знаю.\n\n"
            f"Попробуйте спросить о чем-то другом."
        )
        return [CoreResponse(type="text", content=text)]

    # --- Утилиты ---

    @staticmethod
    def _clean_text_for_comparison(text: str) -> str:
        if not text: return ""
        cleaned_text = re.sub(r'[^a-zA-Zа-яА-Я0-9\s]', '', text)
        return ' '.join(cleaned_text.split()).lower()

    def _find_handler_for_action(self, action: str, primary_entity: dict) -> ActionHandler | None:
        entity_type = primary_entity.get("type", "ANY") if primary_entity else "ANY"
        if entity_type == "Unknown":
            return self._handle_unknown_entity_wrapper
        
        handler = self.action_handlers.get((action, entity_type))
        if handler: return handler
        return self.action_handlers.get((action, "ANY"))

    def _check_button_command(self, query: str, latest_history: dict) -> tuple[bool, CallbackHandler | None]:
        if not latest_history: return False, None
        last_response = latest_history.get("response", [])
        # Проверяем структуру ответа (теперь это может быть CoreResponse в истории)
        # Для упрощения считаем, что история хранит сериализованные CoreResponse (dict)
        
        # Ищем кнопки в последнем сообщении истории
        buttons_data = []
        if last_response and isinstance(last_response[0], dict):
             buttons_data = last_response[0].get("buttons", [])
        
        clean_query = self._clean_text_for_comparison(query)
        for row in buttons_data:
            for button in row:
                if self._clean_text_for_comparison(button.get("text")) == clean_query:
                    callback_data = button.get("callback_data")
                    if callback_data:
                        prefix = callback_data.split(':', 1)[0]
                        return True, self.callback_handlers.get(prefix)
        return False, None
    
    def _get_callback_data_from_history(self, query: str, latest_history: dict) -> str:
        if not latest_history: return ""
        last_response = latest_history.get("response", [])
        buttons_data = []
        if last_response and isinstance(last_response[0], dict):
             buttons_data = last_response[0].get("buttons", [])
        
        clean_query = self._clean_text_for_comparison(query)
        for row in buttons_data:
            for button in row:
                if self._clean_text_for_comparison(button.get("text")) == clean_query:
                    return button.get("callback_data", "")
        return ""

    # --- ГЛАВНЫЙ ЦИКЛ ОБРАБОТКИ ---

    async def process_message(self, message: types.Message):
        user_id = str(message.chat.id)
        query = message.text
        logger.info(f"[{user_id}] Получен запрос: '{query}'")
        
        # FeedbackManager - это Telegram-специфичная утилита
        feedback = FeedbackManager(message)
        
        # Создаем функцию-адаптер для статусов
        async def telegram_status_adapter(text: str):
            await feedback.send_progress_message(text)

        try:
            await feedback.start_action("typing")
            
            # Обработка Override (для рекурсии)
            final_analysis_override = getattr(message, 'final_analysis_override', None)

            if final_analysis_override:
                final_analysis = final_analysis_override
            else:
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                
                # Проверка на нажатие кнопки текстом
                is_button, btn_handler = self._check_button_command(query, latest_history)
                if is_button and btn_handler:
                    data = self._get_callback_data_from_history(query, latest_history)
                    fake_cq = FakeCallbackQuery(message=message, data=data)
                    await btn_handler(fake_cq)
                    return

                await feedback.send_progress_message("🔍 Получил ваш запрос, анализирую...")
                
                analysis = await self.qa.analyze_query(query, history=latest_history)
                if not analysis:
                    await message.answer("Не удалось проанализировать запрос.")
                    return

                final_analysis = await self.dialogue_manager.enrich_request(user_id, analysis, query)
            
            # Выбор обработчика
            handler = None
            action = final_analysis.get("action")
            
            if action == "show_map" and final_analysis.get("used_objects_from_context"):
                handler = handle_draw_map_of_list_stub
            else:
                primary_entity = final_analysis.get("primary_entity")
                handler = self._find_handler_for_action(action, primary_entity)

            if not handler:
                logger.warning(f"[{user_id}] Нет обработчика для {action}")
                unhandled_logger.info(f"USER_ID [{user_id}] - QUERY: \"{query}\"")
                await message.answer("Я пока не умею это делать. Попробуйте переформулировать.")
                return

            # Debug Info
            debug_mode = get_user_settings(user_id).get("debug_mode", False)
            if debug_mode:
                await message.answer(f"🐞 **Debug Analysis**\n```json\n{final_analysis}\n```", parse_mode="Markdown")

            # === ВЫЗОВ ЧИСТОЙ ЛОГИКИ ===
            # Мы передаем только данные и колбэк, никаких message!
            try:
                responses = await handler(
                    session=self.session,
                    analysis=final_analysis,
                    user_id=user_id,
                    original_query=query,
                    debug_mode=debug_mode,
                    on_status=telegram_status_adapter 
                )
            except Exception as e:
                # Механизм отката (Retry with previous action)
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                if latest_history:
                    last_action = latest_history.get("analysis", {}).get("action")
                    if last_action and last_action != final_analysis.get("action"):
                        logger.warning(f"[{user_id}] Ошибка {e}, откат к {last_action}")
                        final_analysis["action"] = last_action
                        message.final_analysis_override = final_analysis
                        await self.process_message(message)
                        return
                raise e

            # Извлекаем used_objects для истории
            used_objects = []
            for resp in responses:
                if resp.used_objects:
                    used_objects.extend(resp.used_objects)

            # === ОТПРАВКА ОТВЕТА В TELEGRAM ===
            await self._send_core_responses(message, responses)
            
            # Сохранение истории (сериализуем CoreResponse в dict)
            responses_dict = [resp.model_dump() for resp in responses]
            await self.dialogue_manager.update_history(user_id, query, final_analysis, responses_dict, used_objects)
            
        except Exception as e:
            logger.error(f"[{user_id}] Error in process_message: {e}", exc_info=True)
            await send_error_log(self.session, query, user_id, e, final_analysis if 'final_analysis' in locals() else {})
            await message.answer("Произошла ошибка при обработке запроса.")
        finally:
            await feedback.cleanup()

    # --- ОТПРАВКА ОТВЕТОВ (RENDERER) ---

    async def _send_core_responses(self, message: types.Message, responses: List[CoreResponse]):
        """Превращает CoreResponse в вызовы aiogram."""
        for resp in responses:
            parse_mode = "HTML"
            
            # 1. Формируем клавиатуру
            keyboard = self._build_telegram_kb(resp.buttons)
            
            # 2. Готовим текст (если это текст или подпись)
            # ВАЖНО: Если это картинка, то resp.content — это ссылка, а не текст!
            text_content = ""
            if resp.type != "image" and resp.content:
                text_content = convert_llm_markdown_to_html(resp.content)
            
            if resp.type == "text":
                await send_long_message(message, text_content, parse_mode=parse_mode, reply_markup=keyboard)
            
            elif resp.type == "image":
                # Для картинок content — это URL. Подпись (caption) пока пустая.
                # Если в будущем захотите подписи к фото, нужно будет добавить поле caption в CoreResponse
                await message.answer_photo(
                    photo=resp.content, 
                    caption=None, # <--- ИСПРАВЛЕНО: убрали дублирование ссылки в подпись
                    reply_markup=keyboard
                )
            
            elif resp.type == "map":
                # Для карт добавляем кнопку "Открыть интерактивную"
                if resp.interactive_map and not keyboard:
                    keyboard = InlineKeyboardMarkup().add(
                        InlineKeyboardButton("🌍 Открыть карту", url=resp.interactive_map)
                    )
                
                await message.answer_photo(
                    photo=resp.static_map,
                    caption=text_content, # Для карт content — это подпись, тут всё ок
                    reply_markup=keyboard,
                    parse_mode=parse_mode
                )
            
            elif resp.type in ["clarification", "clarification_map"]:
                if resp.static_map:
                     await message.answer_photo(
                        photo=resp.static_map,
                        caption=text_content,
                        reply_markup=keyboard,
                        parse_mode=parse_mode
                    )
                else:
                    await message.answer(text_content, reply_markup=keyboard, parse_mode=parse_mode)
            
            elif resp.type == "debug":
                await message.answer(resp.content, parse_mode="Markdown")
                
    @staticmethod
    def _build_telegram_kb(buttons_data: List[List[Dict[str, Any]]]) -> InlineKeyboardMarkup | None:
        if not buttons_data: return None
        kb = InlineKeyboardMarkup()
        for row in buttons_data:
            btn_row = []
            for btn in row:
                btn_row.append(InlineKeyboardButton(
                    text=btn["text"],
                    callback_data=btn.get("callback_data"),
                    url=btn.get("url")
                ))
            kb.row(*btn_row)
        return kb

    # --- ОБРАБОТЧИКИ CALLBACK (ADAPTERS) ---

    async def process_callback(self, callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        try:
            prefix = data.split(':', 1)[0]
            handler = self.callback_handlers.get(prefix)
            if handler:
                await handler(callback_query)
            else:
                await callback_query.answer("Неизвестная команда")
        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            await callback_query.answer("Ошибка обработки")

    async def _handle_pagination(self, cq: types.CallbackQuery):
        user_id = str(cq.from_user.id)
        await cq.answer("Ищу...")
        
        # Получаем контекст из Redis
        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        context_data = await context_manager.get_context(options_key)
        
        if not context_data:
            await cq.message.edit_text("Поиск устарел.")
            return

        term = context_data.get("original_term")
        offset = context_data.get("offset", 0) + len(context_data.get("options", []))
        
        # Формируем анализ для вызова логики
        simulated_analysis = {
            "primary_entity": {"name": term, "type": "Biological"},
            "offset": offset
        }
        
        # Вызываем логику
        responses = await handle_get_description(
            self.session, simulated_analysis, user_id, f"Пагинация: {term}", False
        )
        
        # Рендерим ответ (для пагинации обычно редактируем сообщение)
        if responses and responses[0].type == "clarification":
            resp = responses[0]
            kb = self._build_telegram_kb(resp.buttons)
            text = convert_llm_markdown_to_html(resp.content)
            await cq.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await self._send_core_responses(cq.message, responses)

    async def _handle_exploration(self, cq: types.CallbackQuery):
        user_id = str(cq.from_user.id)
        is_real = isinstance(cq, types.CallbackQuery)
        
        if is_real: 
            await cq.answer("Загрузка...")
            await cq.message.edit_reply_markup(reply_markup=None)

        _, action, geo_place = cq.data.split(':', 2)
        message = cq.message

        # Здесь осталась небольшая логика получения списка для генерации отчета.
        # В идеале это тоже нужно вынести в geospatial.py, но для адаптера допустимо.
        url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
        payload = {"name": geo_place, "buffer_radius_km": 5}
        
        async with self.session.post(url, json=payload) as resp:
            data = await resp.json()
            objects = data.get("all_biological_names", [])

        responses_to_send = []
        
        if action == "full_list":
            text = f"📋 **Объекты в районе «{geo_place}»**:\n\n• " + "\n• ".join(objects[:100])
            responses_to_send.append(CoreResponse(type="text", content=text))
        
        elif action == "overview":
            await message.answer("Готовлю обзор...")
            analysis = await self.qa.analyze_location_objects(geo_place, objects)
            text = f"🌿 **{geo_place}**\n\n{analysis['statistics']}\n\n"
            if analysis.get('interesting_objects'):
                text += "🎯 **Интересные:**\n"
                for item in analysis['interesting_objects']:
                    text += f"• **{item['name']}** - {item['reason']}\n"
            responses_to_send.append(CoreResponse(type="text", content=text))

        await self._send_core_responses(message, responses_to_send)
        
        # Сохранение в историю
        simulated_analysis = {"action": "list_items", "secondary_entity": {"name": geo_place}}
        await self.dialogue_manager.update_history(user_id, f"Exploration {action}", simulated_analysis, [r.model_dump() for r in responses_to_send], [])

    async def _handle_fallback(self, cq: types.CallbackQuery):
        user_id = str(cq.from_user.id)
        is_real = isinstance(cq, types.CallbackQuery)
        if is_real: await cq.answer("Ищу...")
        
        _, fallback_type, object_nom = cq.data.split(':', 2)
        
        context_manager = RedisContextManager()
        fallback_key = f"fallback_attributes:{user_id}"
        attrs = await context_manager.get_context(fallback_key)
        
        if not attrs:
            await cq.message.answer("Контекст устарел.")
            return

        # Модифицируем атрибуты
        if fallback_type == "no_season": attrs.pop("season", None)
        elif fallback_type == "no_habitat": attrs.pop("habitat", None)
        elif fallback_type == "no_fruits": attrs.pop("fruits_present", None)
        elif fallback_type == "no_flowering": attrs.pop("flowering", None)
        
        # Удаляем контекст, чтобы не мусорить (или оставляем, если нужны множественные клики)
        await context_manager.delete_context(fallback_key)

        simulated_analysis = {
            "primary_entity": {"name": object_nom},
            "attributes": attrs
        }
        
        responses = await handle_get_picture(self.session, simulated_analysis, user_id, f"Fallback {object_nom}", False)
        
        if is_real: await cq.message.edit_reply_markup(reply_markup=None)
        await self._send_core_responses(cq.message, responses)

    async def _handle_clarify_by_index(self, cq: types.CallbackQuery):
        user_id = str(cq.from_user.id)
        if isinstance(cq, types.CallbackQuery): await cq.message.edit_reply_markup(reply_markup=None)

        try:
            idx = int(cq.data.split(':', 1)[1])
        except ValueError: return

        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        data = await context_manager.get_context(options_key)
        options = data.get("options", [])

        if not options or idx >= len(options):
            await cq.message.answer("Выбор устарел.")
            return

        selected = options[idx]
        simulated_analysis = {"primary_entity": {"name": selected}}
        
        responses = await handle_get_description(self.session, simulated_analysis, user_id, selected, False)
        
        await context_manager.delete_context(options_key)
        await self._send_core_responses(cq.message, responses)