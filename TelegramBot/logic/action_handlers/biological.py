import aiohttp
import asyncio
import logging
from typing import Dict, Any, Optional
from aiogram import types
from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from utils.settings_manager import get_user_settings
from utils.context_manager import RedisContextManager
from utils.bot_utils import create_structured_response
from utils.feedback_manager import FeedbackManager
from utils.error_logger import send_error_log, log_api_error

logger = logging.getLogger(__name__)

def get_user_fallback_setting(user_id: str) -> bool:
    """Проверяет, включен ли fallback для пользователя."""
    return get_user_settings(user_id).get("gigachat_fallback", False)

async def call_gigachat_fallback_service(session: aiohttp.ClientSession, question: str) -> str | None:
    """Асинхронно делает HTTP-запрос к внешнему GigaChat сервису."""
    url = GIGACHAT_FALLBACK_URL
    try:
        payload = {"question": question}
        async with session.post(url, json=payload, timeout=GIGACHAT_TIMEOUT) as response:
            if response.ok:
                data = await response.json()
                return data.get("answer")
            else:
                logger.error(f"Fallback-сервис GigaChat вернул ошибку: {response.status} {await response.text()}")
                return None
    except Exception as e:
        logger.error(f"Сетевая ошибка при подключении к fallback-сервису GigaChat: {e}")
        return None

async def check_simplified_search(session: aiohttp.ClientSession, object_nom: str, features: dict, debug_mode: bool) -> bool:
    """
    Проверяет, вернет ли упрощенный запрос результаты
    """
    try:
        url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
        payload = {"species_name": object_nom, "features": features}
        
        logger.info(f"Проверка упрощенного запроса: {object_nom} с features: {features}")
        
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                has_images = bool(data.get("images"))
                logger.info(f"Результат проверки для {object_nom} {features}: {has_images}")
                return has_images
            logger.warning(f"API вернул статус {resp.status} для проверки {object_nom}")
            return False
    except asyncio.TimeoutError:
        logger.warning(f"Таймаут при проверке упрощенного запроса для {object_nom}")
        return False
    except Exception as e:
        logger.warning(f"Ошибка проверки упрощенного запроса для {object_nom}: {e}")
        return False

async def handle_get_picture(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str,
    debug_mode: bool = False,
    message: Optional[types.Message] = None
) -> list:
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    attributes = analysis.get("attributes", {})
    
    logger.info(f"[{user_id}] handle_get_picture: объект='{object_nom}', атрибуты={list(attributes.keys())}")
    
    if not object_nom:
        return [{"type": "text", "content": "Не указан объект для поиска изображения."}]

    # Инициализируем FeedbackManager если есть message
    feedback = FeedbackManager(message) if message else None
    
    try:
        # Показываем статус "загружает фото"
        if feedback:
            await feedback.start_action("upload_photo")
            await feedback.send_progress_message(f"📸 Ищу изображения для «{object_nom}»...")
        
        features = {}
        if attributes.get("season"): features["season"] = attributes["season"]
        if attributes.get("habitat"): features["habitat"] = attributes["habitat"]
        if attributes.get("fruits_present"): features["fruits_present"] = attributes["fruits_present"]
        if attributes.get("flowering"): features["flowering"] = attributes["flowering"]

        url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
        payload = {"species_name": object_nom, "features": features}

        responses = []
        if debug_mode:
            debug_info = (
                f"🐞 **API Request (Image Search)**\n"
                f"**URL**: `{url}`\n"
                f"**Payload**:\n```json\n{payload}\n```"
            )
            responses.append({"type": "debug", "content": debug_info})

        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                resp_text = await resp.text()
                await log_api_error(
                    session, user_id, url, resp.status, resp_text, original_query,
                    context=analysis, source="biological.handle_get_picture"
                )
            api_data = await resp.json()

            if not resp.ok or not api_data.get("images"):
                logger.info(f"[{user_id}] Изображения для '{object_nom}' с признаками {features} не найдены. Запуск логики fallback.")
                
                if not attributes:
                    responses.append({"type": "text", "content": f"Извините, я не нашел изображений для «{object_nom}»."})
                    return responses

                # Обновляем сообщение о прогрессе
                if feedback:
                    await feedback.send_progress_message("🔍 Изучаю альтернативные варианты...")

                fallback_options = []
                if "season" in attributes:
                    test_features = features.copy(); test_features.pop("season")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "❄️ Без сезона", "callback_data": f"fallback:no_season:{object_nom}"})
                
                if "habitat" in attributes:
                    test_features = features.copy(); test_features.pop("habitat")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "🌲 Без места", "callback_data": f"fallback:no_habitat:{object_nom}"})

                if "fruits_present" in attributes:
                    test_features = features.copy(); test_features.pop("fruits_present")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "🌰 Без плода", "callback_data": f"fallback:no_fruits:{object_nom}"})

                if "flowering" in attributes:
                    test_features = features.copy(); test_features.pop("flowering")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "🌰 Не цветущий", "callback_data": f"fallback:no_flowering:{object_nom}"})

                if await check_simplified_search(session, object_nom, {}, debug_mode):
                    fallback_options.append({"text": "🖼️ Только объект", "callback_data": f"fallback:basic:{object_nom}"})
                
                if not fallback_options:
                    responses.append({"type": "text", "content": f"Извините, не нашел изображений для «{object_nom}» с любыми комбинациями признаков."})
                    return responses

                context_manager = RedisContextManager()
                fallback_key = f"fallback_attributes:{user_id}"
                await context_manager.set_context(fallback_key, attributes)
                await context_manager.redis_client.expire(fallback_key, 600)
                logger.info(f"[{user_id}] Сохранены атрибуты для fallback: {attributes}")
                
                buttons = [fallback_options[i:i+2] for i in range(0, len(fallback_options), 2)]
                
                responses.append({"type": "clarification", 
                         "content": f"🖼️ К сожалению, у меня нет точных фотографий для вашего запроса.\n\nДавайте попробуем упростить? Вот что я нашел:",
                         "buttons": buttons})
                return responses
            
            user_messages = []
            images = api_data.get("images", [])
            
            user_messages = [{"type": "image", "content": img["image_path"]} for img in images[:5] if isinstance(img, dict) and "image_path" in img]
            
            if not user_messages:
                logger.warning(f"[{user_id}] Изображения не найдены для '{object_nom}'")
                responses.append({"type": "text", "content": f"Извините, не удалось загрузить ни одного изображения для «{object_nom}»."})
                return responses
            
            logger.info(f"[{user_id}] Найдено {len(user_messages)} изображений для '{object_nom}'")
            responses.extend(create_structured_response(api_data, user_messages))
            return responses

    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_picture: {e}", exc_info=True)
        await send_error_log(
            session=session,
            user_query=original_query,
            user_id=user_id,
            error=e,
            context=analysis,
            additional_info={"source": "biological.handle_get_picture"}
        )
        responses.append({"type": "text", "content": "Произошла внутренняя ошибка при поиске изображений."})
        return responses
    finally:
        if feedback:
            await feedback.stop_action()
    
    
async def handle_get_description(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str, 
    debug_mode: bool,
    message: Optional[types.Message] = None
) -> list:
    """
    Обрабатывает запрос на получение текстового описания объекта.
    - Распознает неоднозначные ответы от API.
    - Формирует кнопки для уточнения, используя Redis для хранения контекста.
    - Поддерживает пагинацию ("Поискать еще").
    - В случае отсутствия информации может использовать GigaChat fallback.
    """
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    offset = analysis.get("offset", 0)
    
    logger.info(f"[{user_id}] handle_get_description: объект='{object_nom}', offset={offset}")

    if not object_nom:
        return [{"type": "text", "content": "Не указан объект для поиска описания."}]
    
    # Инициализируем FeedbackManager если есть message
    feedback = FeedbackManager(message) if message else None
    
    try:
        # Показываем статус "печатает"
        if feedback:
            await feedback.start_action("typing")
            await feedback.send_progress_message(f"🔍 Ищу описание для «{object_nom}»...")
        
        find_url = f"{API_URLS['find_species_with_description']}"
        payload = {"name": object_nom, "limit": 4, "offset": offset} 
        logger.info(f"[{user_id}] Запрос к find_species_with_description: name='{object_nom}', offset={offset}")

        responses = []
        if debug_mode:
            debug_info = (
                f"🐞 **API Request (Find Species)**\n"
                f"**URL**: `{find_url}`\n"
                f"**Payload**:\n```json\n{payload}\n```"
            )
            responses.append({"type": "debug", "content": debug_info})

        async with session.post(find_url, json=payload, timeout=DEFAULT_TIMEOUT) as find_resp:
            if not find_resp.ok:
                error_text = find_resp.text()
                logger.info(f"[{user_id}] API `find_species` вернул ошибку {find_resp.status} для '{object_nom}'")
                await log_api_error(
                    session, user_id, find_url, find_resp.status, error_text, original_query,
                    context=analysis, source="biological.find_species_with_description"
                )
                responses.append({"type": "text", "content": f"Извините, произошла ошибка при поиске «{object_nom}»."})
                return responses
            
            data = await find_resp.json()
            status = data.get("status")
            logger.info(f"[{user_id}] Ответ find_species: status='{status}', найдено совпадений={len(data.get('matches', []))}")

            if status == "ambiguous":
                matches = data.get("matches", [])
                
                context_to_save = {
                    "options": matches,
                    "original_term": object_nom, 
                    "offset": offset             
                }

                context_manager = RedisContextManager()
                options_key = f"clarify_options:{user_id}"
                await context_manager.set_context(options_key, context_to_save)
                await context_manager.redis_client.expire(options_key, 300)

                buttons = []
                for i, match_name in enumerate(matches):
                    buttons.append([{"text": match_name, "callback_data": f"clarify_idx:{i}"}])
                
                system_buttons_row = []
                
                if matches:
                    system_buttons_row.append({"text": "🎲 Любую", "callback_data": "clarify_idx:0"})
                if data.get("has_more", False):
                    system_buttons_row.append({"text": "🔍 Поискать еще", "callback_data": "clarify_more"})
                if system_buttons_row:
                    buttons.append(system_buttons_row)

                responses.append({
                    "type": "clarification",
                    "content": f"Я знаю несколько видов для «{object_nom}». Уточните, какой именно вас интересует?",
                    "buttons": buttons
                })
                return responses
            
            elif status == "found":
                canonical_name = data.get("matches", [object_nom])[0]
                user_settings = get_user_settings(user_id)
                stoplist_enabled = user_settings.get("stoplist_enabled", True)
                stoplist_param = 1 if stoplist_enabled else 2
            
                desc_url = (f"{API_URLS['get_description']}?species_name={canonical_name}"
                            f"&debug_mode={str(debug_mode).lower()}"
                            f"&in_stoplist={stoplist_param}"
                            f"&query={original_query}")
                
                logger.info(f"[{user_id}] Объект найден: '{canonical_name}'. Запрос описания по URL: {desc_url}")

                if debug_mode:
                    debug_info = (
                        f"🐞 **API Request (Get Description)**\n"
                        f"**URL**: `{desc_url}`"
                    )
                    responses.append({"type": "debug", "content": debug_info})

                async with session.get(desc_url, timeout=DEFAULT_TIMEOUT) as desc_resp:
                    if desc_resp.ok:
                        api_data = await desc_resp.json()
                        
                        user_messages = []
                        text = ""
                        descriptions = api_data.get("descriptions", [])
                        
                        if descriptions:
                            first_item = descriptions[0]
                            if isinstance(first_item, dict):
                                text = first_item.get("content", "")
                            elif isinstance(first_item, str):
                                text = first_item
                        
                        if text:
                            logger.info(f"[{user_id}] Описание для '{canonical_name}' успешно найдено.")
                            user_messages.append({"type": "text", "content": text})
                        
                        responses.extend(create_structured_response(api_data, user_messages))
                        return responses
                    
                    else:
                        error_text = await desc_resp.text()
                        await log_api_error(
                            session, user_id, desc_url, desc_resp.status, error_text, original_query,
                            context={"canonical_name": canonical_name}, source="biological.get_description"
                        )

                        if desc_resp.status == 400:
                            try:
                                desc_data = await desc_resp.json()
                                responses.append({"type": "text", "content": desc_data.get("error", "Я не смог найти ответ")})
                                return responses
                            except: pass

            logger.warning(f"[{user_id}] Описание для '{object_nom}' не найдено.")
            error_text = f"Not found for '{object_nom}'"
            await log_api_error(
                    session, user_id, find_url, find_resp.status, error_text, original_query,
                    context=analysis, source="biological.find_species_with_description"
                )
            if get_user_fallback_setting(user_id):
                fallback_answer = await call_gigachat_fallback_service(session, original_query)
                if fallback_answer: 
                    responses.append({"type": "text", "content": f"**Ответ от GigaChat:**\n\n{fallback_answer}", "parse_mode": "Markdown"})
                    return responses
            
            responses.append({"type": "text", "content": f"К сожалению, у меня нет описания для «{object_nom}»."})
            return responses

    except Exception as e:
        logger.error(f"[{user_id}] Критическая ошибка в `handle_get_description`: {e}", exc_info=True)
        await send_error_log(
            session=session, user_query=original_query, user_id=user_id, error=e,
            context=analysis, additional_info={"source": "biological.handle_get_description"}
        )
        responses.append({"type": "text", "content": "Проблема с подключением к серверу описаний."})
        return responses
    finally:
        if feedback:
            await feedback.stop_action()
    
