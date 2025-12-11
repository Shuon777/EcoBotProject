import logging
import aiohttp
import inspect
from typing import Dict, Any, Callable, Awaitable
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import re

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from logic.action_handlers.biological import handle_get_description, handle_get_picture
from logic.action_handlers.geospatial import (
    handle_draw_locate_map, handle_nearest, handle_objects_in_polygon,
    handle_geo_request, handle_draw_map_of_infrastructure, handle_draw_map_of_list_stub
)
from utils.bot_utils import send_long_message, convert_llm_markdown_to_html
from utils.settings_manager import get_user_settings
from utils.context_manager import RedisContextManager
from utils.feedback_manager import FeedbackManager
from utils.error_logger import send_error_log
from config import API_URLS

unhandled_logger = logging.getLogger("unhandled")
logger = logging.getLogger(__name__)

ActionHandler = Callable[[Dict[str, Any], str, str], Awaitable[list]]
CallbackHandler = Callable[[types.CallbackQuery], Awaitable[None]]


class FakeCallbackQuery:
    """Имитирует CallbackQuery для обработки текстовых команд как кнопок."""
    def __init__(self, message: types.Message, data: str):
        self.message = message
        self.data = data
        self.from_user = message.from_user
    
    async def answer(self, *args, **kwargs):
        """Заглушка для совместимости с реальным CallbackQuery."""
        pass


class GigaChatHandler:
    """Обработчик сообщений для режима GigaChat с поддержкой LLM-анализа и диалогового контекста."""
    
    def __init__(self, qa: QueryAnalyzer, dialogue_manager: DialogueManager, session: aiohttp.ClientSession):
        self.qa = qa
        self.dialogue_manager = dialogue_manager
        self.session = session
        
        # Маппинг действий и типов сущностей на обработчики
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
            ("get_help", "ANY"): self._handle_help_request,
            ("small_talk", "ANY"): self._handle_small_talk_request,
        
        }

        # Маппинг префиксов callback_data на обработчики
        self.callback_handlers: Dict[str, CallbackHandler] = {
            "clarify_idx": self._handle_clarify_by_index,
            "clarify_more": self._handle_pagination,
            "explore": self._handle_exploration,
            "fallback": self._handle_fallback,
        }
    
    async def _handle_help_request(self, original_query: str, **kwargs) -> list:
        """
        Обрабатывает запросы типа 'Что ты умеешь?', вызывая генерацию текста через LLM.
        """
        # Генерируем ответ с помощью метода в QueryAnalyzer, который содержит системный промпт
        answer_text = await self.qa.answer_general_question(original_query)
        
        # Возвращаем в стандартном формате ответов
        return [{"type": "text", "content": answer_text}]
    
    async def _handle_small_talk_request(self, original_query: str, **kwargs) -> list:
        """
        Обрабатывает small_talk (приветствия и оффтоп).
        """
        answer = await self.qa.reply_to_small_talk(original_query)
        return [{"type": "text", "content": answer}]

    @staticmethod
    def _clean_text_for_comparison(text: str) -> str:
        """Нормализует текст для сравнения: удаляет спецсимволы, приводит к lowercase."""
        if not text:
            return ""
        # Оставляем только буквы (русские и латинские), цифры и пробелы
        cleaned_text = re.sub(r'[^a-zA-Zа-яА-Я0-9\s]', '', text)
        # Заменяем несколько пробелов на один и убираем по краям
        return ' '.join(cleaned_text.split()).lower()

    def _find_handler_for_action(self, action: str, primary_entity: dict) -> ActionHandler | None:
        """Находит подходящий обработчик для действия и типа сущности."""
        
        # [НОВОЕ] Перехват Unknown
        entity_type = primary_entity.get("type", "ANY") if primary_entity else "ANY"
        entity_name = primary_entity.get("name", "") if primary_entity else ""

        if entity_type == "Unknown":
            # Используем lambda или partial, чтобы адаптировать сигнатуру, 
            # или просто вернем функцию, которая принимает **kwargs
            return self._handle_unknown_entity_wrapper
            
        # ... старый код ...
        # Сначала ищем точное совпадение (action, entity_type)
        handler = self.action_handlers.get((action, entity_type))
        if handler:
            return handler
        
        # Затем ищем общий обработчик (action, "ANY")
        return self.action_handlers.get((action, "ANY"))

    def _check_button_command(self, query: str, latest_history: dict) -> tuple[bool, CallbackHandler | None]:
        """Проверяет, является ли текст командой из последней клавиатуры."""
        if not latest_history:
            return False, None
        
        last_response = latest_history.get("response", [])
        if not last_response or not last_response[0].get("buttons"):
            return False, None
        
        buttons_data = last_response[0]["buttons"]
        clean_query = self._clean_text_for_comparison(query)
        
        for row in buttons_data:
            for button in row:
                clean_button_text = self._clean_text_for_comparison(button.get("text"))
                if clean_button_text and clean_button_text == clean_query:
                    callback_data = button.get("callback_data")
                    if callback_data:
                        prefix = callback_data.split(':', 1)[0]
                        handler = self.callback_handlers.get(prefix)
                        return True, handler
        
        return False, None

    async def process_message(self, message: types.Message):
        """
        Главный обработчик текстовых сообщений.
        
        Выполняет:
        1. Анализ запроса через LLM
        2. Обогащение анализа диалоговым контекстом
        3. Диспетчеризацию на соответствующий обработчик
        4. Отправку ответа пользователю
        5. Сохранение истории диалога
        """
        user_id = str(message.chat.id)
        query = message.text
        
        logger.info(f"[{user_id}] Получен запрос: '{query}'")
        
        feedback = FeedbackManager(message)
        
        try:
            await feedback.start_action("typing")
            
            # Проверка на override-анализ (для рекурсивных вызовов при откате)
            final_analysis_override = getattr(message, 'final_analysis_override', None)

            if final_analysis_override:
                final_analysis = final_analysis_override
                logger.info(f"[{user_id}] Используется override-анализ после отката")
                delattr(message, 'final_analysis_override')
            else:
                # Стандартный пайплайн обработки
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                
                # Проверка: текст может быть командой из последней клавиатуры
                is_button_cmd, button_handler = self._check_button_command(query, latest_history)
                if is_button_cmd and button_handler:
                    logger.info(f"[{user_id}] Текст '{query}' распознан как кнопка, вызов обработчика {button_handler.__name__}")
                    fake_cq = FakeCallbackQuery(message=message, data=self._get_callback_data(query, latest_history))
                    await button_handler(fake_cq)
                    return

                await feedback.send_progress_message("🔍 Получил ваш запрос, анализирую...")
                
                # Шаг 1: LLM-анализ запроса
                analysis = await self.qa.analyze_query(query, history=latest_history)
                if not analysis:
                    logger.warning(f"[{user_id}] QueryAnalyzer не вернул анализ для: '{query}'")
                    await self._reply_with_error(message, "QueryAnalyzer вернул пустой анализ")
                    return

                # Шаг 2: Обогащение анализа контекстом
                final_analysis = await self.dialogue_manager.enrich_request(user_id, analysis, query)
            
            logger.info(f"[{user_id}] Финальный анализ - action: {final_analysis.get('action')}, entity: {(final_analysis.get('primary_entity') or {}).get('name')}")
            
            # Debug mode
            debug_mode = get_user_settings(user_id).get("debug_mode", False)
            if debug_mode:
                debug_info = f"🐞 **Debug Info**\n**LLM Analysis**:\n```json\n{final_analysis}\n```"
                await message.answer(debug_info, parse_mode="Markdown")

            # Шаг 3: Выбор обработчика
            handler = None
            action = final_analysis.get("action")
            
            # Специальный случай: карта из контекста
            if action == "show_map" and final_analysis.get("used_objects_from_context"):
                handler = handle_draw_map_of_list_stub
                logger.info(f"[{user_id}] Контекстный запрос на карту -> handle_draw_map_of_list_stub")
            else:
                primary_entity = final_analysis.get("primary_entity")
                handler = self._find_handler_for_action(action, primary_entity)

            if not handler:
                logger.warning(f"[{user_id}] Не найден обработчик для action='{action}'")
                unhandled_logger.info(f"USER_ID [{user_id}] - QUERY: \"{query}\" - action: {action}")
                
                fallback_keyboard = types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton(text="💡 Начать поиск с подсказками", switch_inline_query_current_chat="")
                )
                await message.answer(
                    "К сожалению, я не смог распознать ваш запрос. Попробуйте переформулировать запрос или использовать быстрый поиск с автодополнением.",
                    reply_markup=fallback_keyboard
                )
                return

            logger.info(f"[{user_id}] Вызов обработчика: {handler.__name__}")

            if debug_mode:
                await message.answer(f"🐞 **Handler Selected**: `{handler.__name__}`", parse_mode="Markdown")

            # Шаг 4: Вызов обработчика с автоопределением параметров
            responses = []
            try:
                # Подготовка всех возможных аргументов
                all_possible_args = {
                    "session": self.session,
                    "analysis": final_analysis,
                    "user_id": user_id,
                    "original_query": query,
                    "debug_mode": debug_mode,
                    "message": message
                }
                
                # Автоопределение нужных аргументов через introspection
                handler_signature = inspect.signature(handler)
                required_args = handler_signature.parameters.keys()
                args_to_pass = {key: value for key, value in all_possible_args.items() if key in required_args}
                
                responses = await handler(**args_to_pass)
            
            except (AttributeError, TypeError, KeyError) as e:
                logger.error(f"[{user_id}] Ошибка вызова {handler.__name__}: {e}", exc_info=False)
                
                # Механизм отката: пытаемся использовать предыдущее действие
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                if latest_history:
                    last_action = latest_history.get("analysis", {}).get("action")
                    if last_action and last_action != final_analysis.get("action"):
                        logger.warning(f"[{user_id}] Откат к предыдущему action: '{last_action}'")
                        final_analysis["action"] = last_action
                        message.final_analysis_override = final_analysis
                        await self.process_message(message)  # Рекурсивный вызов
                        return

                responses = [{"type": "text", "content": "Извините, я не смог обработать ваш уточняющий запрос."}]

            # Шаг 5: Извлечение метаданных и отправка ответа
            used_objects = []
            if responses and isinstance(responses, list) and responses[0].get('used_objects'):
                used_objects = responses[0].pop('used_objects')
                logger.info(f"[{user_id}] Извлечено {len(used_objects)} used_objects для контекста")
                
            await self._send_responses(message, responses)
            
            # Шаг 6: Сохранение в историю
            analysis_to_save = getattr(message, 'final_analysis_override', final_analysis)
            await self.dialogue_manager.update_history(user_id, query, analysis_to_save, responses, used_objects)
            
            logger.info(f"[{user_id}] Запрос успешно обработан")
            
        except Exception as e:
            logger.error(f"[{user_id}] КРИТИЧЕСКАЯ ОШИБКА в process_message: {e}", exc_info=True)
            try:
                # Пытаемся получить контекст (историю), чтобы приложить к логу
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                await send_error_log(
                    session=self.session,
                    user_query=query,
                    user_id=user_id,
                    error=e,
                    context=latest_history or {},
                    additional_info={"source": "gigachat_handler.process_message"}
                )
            except Exception as log_ex:
                logger.error(f"Ошибка при попытке отправить лог ошибки: {log_ex}")
            await message.answer("Ой, что-то пошло не так на моей стороне.")
        finally:
            await feedback.cleanup()

    def _get_callback_data(self, query: str, latest_history: dict) -> str:
        """Извлекает callback_data для кнопки, соответствующей тексту запроса."""
        if not latest_history:
            return ""
        
        last_response = latest_history.get("response", [])
        if not last_response or not last_response[0].get("buttons"):
            return ""
        
        buttons_data = last_response[0]["buttons"]
        clean_query = self._clean_text_for_comparison(query)
        
        for row in buttons_data:
            for button in row:
                clean_button_text = self._clean_text_for_comparison(button.get("text"))
                if clean_button_text and clean_button_text == clean_query:
                    return button.get("callback_data", "")
        
        return ""

    async def process_callback(self, callback_query: types.CallbackQuery):
        """Главный обработчик кнопок. Диспетчеризует по префиксу callback_data."""
        user_id = str(callback_query.from_user.id)
        data = callback_query.data
        
        logger.info(f"[{user_id}] Получен callback: {data}")
        
        try:
            prefix = data.split(':', 1)[0]
            handler = self.callback_handlers.get(prefix)

            if handler:
                logger.info(f"[{user_id}] Вызов callback-обработчика: {handler.__name__}")
                await handler(callback_query)
            else:
                logger.warning(f"[{user_id}] Неизвестный callback префикс: '{prefix}'")
                await callback_query.answer("Это действие больше не поддерживается.", show_alert=True)

        except Exception as e:
            logger.error(f"[{user_id}] Ошибка в process_callback для data='{data}': {e}", exc_info=True)
            try:
                latest_history = await self.dialogue_manager.get_latest_history(user_id)
                await send_error_log(
                    session=self.session,
                    user_query=data,
                    user_id=user_id,
                    error=e,
                    context=latest_history or {},
                    additional_info={"source": "gigachat_handler.process_callback"}
                )
            except Exception:
                pass
            await callback_query.message.answer("Произошла ошибка при обработке вашего выбора.")
            await callback_query.answer()

    async def _send_responses(self, message: types.Message, responses: list):
        """Отправляет отформатированные ответы пользователю с экранированием Markdown."""
        for resp_data in responses:
            response_type = resp_data.get("type")
            
            # Используем HTML для корректного отображения жирного текста и заголовков
            parse_mode = "HTML"

            if response_type in ["clarification", "clarification_map"]:
                keyboard = self._build_keyboard(resp_data.get("buttons"))
                # Конвертируем Markdown в HTML
                caption_text = convert_llm_markdown_to_html(resp_data.get("content", ""))
                
                if response_type == "clarification_map":
                    await message.answer_photo(
                        photo=resp_data["static_map"],
                        caption=caption_text,
                        reply_markup=keyboard,
                        parse_mode=parse_mode
                    )
                else:
                    await message.answer(caption_text, reply_markup=keyboard, parse_mode=parse_mode)
                break
            
            elif response_type == "text":
                # Конвертируем Markdown в HTML
                content_text = convert_llm_markdown_to_html(resp_data.get("content", ""))
                await send_long_message(message, content_text, parse_mode=parse_mode)
                
            elif response_type == "image":
                await message.answer_photo(resp_data["content"])
                
            elif response_type == "map":
                kb = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("Открыть интерактивную карту 🌐", url=resp_data["interactive"])
                )
                
                # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
                # Инициализируем caption_text ДО использования
                raw_caption = resp_data.get("caption", "")
                caption_text = convert_llm_markdown_to_html(raw_caption)
                
                await message.answer_photo(
                    photo=resp_data["static"],
                    caption=caption_text,
                    reply_markup=kb,
                    parse_mode=parse_mode
                )

            elif response_type == "debug":
                # Для debug используем Markdown (V1), так как там часто JSON блоки
                content = resp_data.get("content", "")
                await message.answer(content, parse_mode="Markdown")
                
    @staticmethod
    def _build_keyboard(buttons_data: list) -> InlineKeyboardMarkup | None:
        """Универсальный сборщик инлайн-клавиатур из данных кнопок."""
        if not buttons_data:
            return None
        
        kb = InlineKeyboardMarkup()
        for row in buttons_data:
            button_row = [
                InlineKeyboardButton(
                    text=btn["text"],
                    callback_data=btn.get("callback_data"),
                    url=btn.get("url")
                )
                for btn in row
            ]
            kb.row(*button_row)
        return kb

    async def _reply_with_error(self, message: types.Message, log_text: str, reply_text: str = "Произошла ошибка."):
        """Отправляет сообщение об ошибке пользователю и логирует."""
        logger.warning(f"[{message.chat.id}] {log_text}")
        await message.answer(reply_text)

    async def _handle_pagination(self, cq: types.CallbackQuery):
        """Обрабатывает кнопку 'Поискать еще' для пагинации результатов поиска."""
        user_id = str(cq.from_user.id)
        logger.info(f"[{user_id}] Запрос пагинации")
        
        await cq.answer("Ищу дальше...")

        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        context_data = await context_manager.get_context(options_key)

        if not context_data:
            logger.warning(f"[{user_id}] Контекст для пагинации не найден в Redis")
            await cq.message.edit_text("Извините, этот поиск уже неактуален. Пожалуйста, повторите ваш запрос.")
            return

        ambiguous_term = context_data.get("original_term")
        current_offset = context_data.get("offset", 0)
        options_count = len(context_data.get("options", []))
        
        if not ambiguous_term:
            logger.warning(f"[{user_id}] Отсутствует original_term в контексте пагинации")
            await cq.message.edit_text("Произошла ошибка: не удалось найти исходный запрос для продолжения поиска.")
            return

        new_offset = current_offset + options_count
        logger.info(f"[{user_id}] Пагинация для '{ambiguous_term}', offset: {current_offset} -> {new_offset}")
        
        simulated_analysis = {
            "action": "describe",
            "primary_entity": {"name": ambiguous_term, "type": "Biological"},
            "offset": new_offset
        }

        debug_mode = get_user_settings(user_id).get("debug_mode", False)
        responses = await handle_get_description(
            self.session, simulated_analysis, user_id, f"Пагинация: {ambiguous_term}", debug_mode
        )
        
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
        Обрабатывает нажатие на кнопки 'Умный обзор' или 'Полный список'.
        Может быть вызван как через CallbackQuery, так и через FakeCallbackQuery.
        """
        user_id = str(cq.from_user.id)
        is_real_callback = isinstance(cq, types.CallbackQuery)

        if is_real_callback:
            await cq.answer("Загружаю данные...")
            await cq.message.edit_reply_markup(reply_markup=None)
        
        _, action, geo_place = cq.data.split(':', 2)
        logger.info(f"[{user_id}] Exploration: action={action}, place={geo_place}")
        
        url = f"{API_URLS['objects_in_polygon']}?debug_mode=false"
        payload = {"name": geo_place, "buffer_radius_km": 5}
        
        message_to_reply = cq.message

        async with self.session.post(url, json=payload) as resp:
            if not resp.ok:
                logger.warning(f"[{user_id}] Ошибка API objects_in_polygon: {resp.status}")
                await message_to_reply.answer("Не удалось получить данные о локации.")
                return
            
            api_data = await resp.json()
            objects_list = api_data.get("all_biological_names", [])

        if not objects_list:
            logger.info(f"[{user_id}] В районе '{geo_place}' не найдено объектов")
            await message_to_reply.answer(f"В районе «{geo_place}» не найдено объектов для обзора.")
            return

        logger.info(f"[{user_id}] Найдено {len(objects_list)} объектов в '{geo_place}'")
        
        simulated_query = f"Пользователь выбрал '{action}' для локации '{geo_place}'"
        simulated_analysis = {
            "action": "list_items",
            "primary_entity": None,
            "secondary_entity": {"name": geo_place, "type": "GeoPlace"}
        }
        response_to_save = []
        
        if action == "full_list":
            # Ограничиваем список первыми 100 объектами для ускорения отправки
            max_items = 100
            items_to_show = objects_list[:max_items]
            
            text = f"📋 **Объекты в районе «{geo_place}»**:\n\n• " + "\n• ".join(items_to_show)
            
            if len(objects_list) > max_items:
                text += f"\n\n_... и ещё {len(objects_list) - max_items} объектов. Используйте 'Умный обзор' для анализа._"
            
            logger.info(f"[{user_id}] Отправка списка: показано {len(items_to_show)} из {len(objects_list)} объектов")
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
        """Обрабатывает упрощенный поиск изображений (без сезона/места/признаков)."""
        user_id = str(cq.from_user.id)
        is_real_callback = isinstance(cq, types.CallbackQuery)
        
        if is_real_callback:
            await cq.message.edit_reply_markup(reply_markup=None)
            await cq.answer("Ищу упрощенный вариант...")
        
        _, fallback_type, object_nom = cq.data.split(':', 2)
        logger.info(f"[{user_id}] Fallback для '{object_nom}', тип: {fallback_type}")

        context_manager = RedisContextManager()
        fallback_key = f"fallback_attributes:{user_id}"
        original_attributes = await context_manager.get_context(fallback_key)
        
        if not original_attributes:
            logger.warning(f"[{user_id}] Не найдены атрибуты для fallback в Redis")
            await self._reply_with_error(
                cq.message,
                f"Fallback-контекст утерян (key: {fallback_key})",
                "Ошибка: контекст для упрощения запроса утерян. Попробуйте снова."
            )
            return

        # Упрощаем атрибуты в зависимости от выбора
        simplified_attributes = original_attributes.copy()
        if fallback_type == "no_season":
            simplified_attributes.pop("season", None)
        elif fallback_type == "no_habitat":
            simplified_attributes.pop("habitat", None)
        elif fallback_type == "no_fruits":
            simplified_attributes.pop("fruits_present", None)
        elif fallback_type == "no_flowering":
            simplified_attributes.pop("flowering", None)

        simplified_analysis = {
            "action": "show_image",
            "primary_entity": {"name": object_nom, "type": "Biological"},
            "attributes": simplified_attributes,
        }
        
        await context_manager.delete_context(fallback_key)

        debug_mode = get_user_settings(user_id).get("debug_mode", False)
        logger.info(f"[{user_id}] Повторный поиск изображений с упрощенными атрибутами: {list(simplified_attributes.keys())}")
        
        responses = await handle_get_picture(self.session, simplified_analysis, user_id, debug_mode)
        simulated_query = f"Упрощенный запрос (fallback): {object_nom}"
        await self.dialogue_manager.update_history(user_id, simulated_query, simplified_analysis, responses)

        await self._send_responses(cq.message, responses)
    
    async def _handle_clarify_by_index(self, cq: types.CallbackQuery):
        """Обрабатывает выбор конкретного варианта из списка уточнений."""
        user_id = str(cq.from_user.id)
        is_real_callback = isinstance(cq, types.CallbackQuery)
        
        if is_real_callback:
            await cq.message.edit_reply_markup(reply_markup=None)
        
        try:
            selected_index = int(cq.data.split(':', 1)[1])
        except (ValueError, IndexError):
            logger.warning(f"[{user_id}] Некорректные данные кнопки: {cq.data}")
            await cq.answer("Ошибка в данных кнопки.", show_alert=True)
            return

        context_manager = RedisContextManager()
        options_key = f"clarify_options:{user_id}"
        context_data = await context_manager.get_context(options_key)
        options = context_data.get("options", []) if context_data else []

        if not options or selected_index >= len(options):
            logger.warning(f"[{user_id}] Контекст уточнений устарел или index={selected_index} out of range")
            await cq.message.answer("Извините, этот выбор уже неактуален. Пожалуйста, повторите ваш запрос.")
            await cq.answer()
            return

        selected_object = options[selected_index]
        logger.info(f"[{user_id}] Выбран вариант: '{selected_object}' (index={selected_index})")
        
        await cq.answer(f"Выбрано: {selected_object}")

        debug_mode = get_user_settings(user_id).get("debug_mode", False)
        simulated_analysis = {
            "action": "describe",
            "primary_entity": {"name": selected_object, "type": "Biological"}
        }
        
        responses = await handle_get_description(
            self.session, simulated_analysis, user_id, f"Уточнение: {selected_object}", debug_mode
        )
        
        simulated_query = f"Выбор из уточнений: {selected_object}"
        await self.dialogue_manager.update_history(user_id, simulated_query, simulated_analysis, responses)
        await self._send_responses(cq.message, responses)
        await context_manager.delete_context(options_key)

        
    async def _handle_unknown_entity(self, message: types.Message, entity_name: str, **kwargs):
            """Обработчик для сущностей, которые не относятся к домену Байкала."""
            text = (
                f"🤔 Я изучаю Байкал, но про «{entity_name}» в контексте "
                f"флоры, фауны или достопримечательностей озера я ничего не знаю.\n\n"
                f"Попробуйте спросить о чем-то другом."
            )
            return [{"type": "text", "content": text}] # Возвращаем для истории
    
    async def _handle_unknown_entity_wrapper(self, message: types.Message, analysis: dict, **kwargs):
        """Обертка, чтобы сигнатура совпадала с другими хендлерами."""
        entity_name = analysis.get("primary_entity", {}).get("name", "этот объект")
        return await self._handle_unknown_entity(message, entity_name)

  