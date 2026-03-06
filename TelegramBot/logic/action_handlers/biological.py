import aiohttp
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable

from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from utils.settings_manager import get_user_settings
from utils.context_manager import RedisContextManager
from utils.error_logger import log_critical, log_api_fail, log_zero_results

# Импортируем нашу новую модель
from core.model import CoreResponse

logger = logging.getLogger(__name__)

# --- Вспомогательные функции (Остались без изменений) ---

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
                logger.error(f"Fallback-сервис GigaChat вернул ошибку: {response.status}")
                return None
    except Exception as e:
        logger.error(f"Сетевая ошибка при подключении к fallback-сервису GigaChat: {e}")
        return None

async def check_simplified_search(session: aiohttp.ClientSession, object_nom: str, features: dict, debug_mode: bool) -> bool:
    """
    Проверяет, вернет ли упрощенный запрос результаты.
    Используется для генерации умных кнопок 'Попробовать без сезона' и т.д.
    """
    try:
        url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
        payload = {"species_name": object_nom, "features": features}
        
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return bool(data.get("images"))
            return False
    except Exception as e:
        logger.warning(f"Ошибка проверки упрощенного запроса для {object_nom}: {e}")
        return False

# --- Основные обработчики (Refactored) ---

async def handle_get_picture(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str,
    debug_mode: bool = False,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    attributes = analysis.get("attributes", {})
    
    logger.info(f"[{user_id}] handle_get_picture: объект='{object_nom}'")
    
    if not object_nom:
        return [CoreResponse(type="text", content="Не указан объект для поиска изображения.")]

    try:
        if on_status:
            await on_status(f"📸 Ищу изображения для «{object_nom}»...")
        
        features = {}
        # Переносим атрибуты в features
        for key in ["season", "habitat", "fruits_present", "flowering"]:
            if attributes.get(key):
                features[key] = attributes[key]

        url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
        payload = {"species_name": object_nom, "features": features}

        responses = []
        if debug_mode:
            responses.append(CoreResponse(
                type="debug", 
                content=f"🐞 **API Request**\nURL: `{url}`\nPayload: `{payload}`"
            ))

        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                resp_text = await resp.text()
                await log_api_fail(session, user_id, url, resp.status, resp_text, original_query, context=analysis)
            
            api_data = await resp.json() if resp.ok else {}

            # ЛОГИКА FALLBACK (если картинки не найдены)
            if not resp.ok or not api_data.get("images"):
                logger.info(f"[{user_id}] Изображения не найдены. Запуск fallback-анализа.")
                
                if resp.ok and not api_data.get("images"):
                    await log_zero_results(
                        session, original_query, user_id, 
                        action="show_image", 
                        search_params={"object": object_nom, "features": features}, 
                        context=analysis
                    )
                
                if not attributes:
                    return [CoreResponse(type="text", content=f"Извините, я не нашел изображений для «{object_nom}».")]

                if on_status:
                    await on_status("🔍 Изучаю альтернативные варианты...")

                # Генерируем кнопки упрощения
                fallback_tasks = []
                options_meta = []

                if "season" in attributes:
                    tf = features.copy(); tf.pop("season")
                    fallback_tasks.append(check_simplified_search(session, object_nom, tf, debug_mode))
                    options_meta.append({"text": "❄️ Без сезона", "callback_data": f"fallback:no_season:{object_nom}"})
                
                if "habitat" in attributes:
                    tf = features.copy(); tf.pop("habitat")
                    fallback_tasks.append(check_simplified_search(session, object_nom, tf, debug_mode))
                    options_meta.append({"text": "🌲 Без места", "callback_data": f"fallback:no_habitat:{object_nom}"})

                if "fruits_present" in attributes:
                    tf = features.copy(); tf.pop("fruits_present")
                    fallback_tasks.append(check_simplified_search(session, object_nom, tf, debug_mode))
                    options_meta.append({"text": "🌰 Без плода", "callback_data": f"fallback:no_fruits:{object_nom}"})
                
                if "flowering" in attributes:
                    tf = features.copy(); tf.pop("flowering")
                    fallback_tasks.append(check_simplified_search(session, object_nom, tf, debug_mode))
                    options_meta.append({"text": "🌸 Не цветущий", "callback_data": f"fallback:no_flowering:{object_nom}"})

                # Базовый поиск
                fallback_tasks.append(check_simplified_search(session, object_nom, {}, debug_mode))
                options_meta.append({"text": "🖼️ Только объект", "callback_data": f"fallback:basic:{object_nom}"})
                
                # Ждем выполнения всех запросов ОДНОВРЕМЕННО
                results = await asyncio.gather(*fallback_tasks)
                
                # Собираем только те кнопки, где API вернул True
                fallback_options = [meta for meta, is_valid in zip(options_meta, results) if is_valid]
                
                if not fallback_options:
                    return [CoreResponse(type="text", content=f"Извините, не нашел изображений для «{object_nom}» с любыми комбинациями признаков.")]

                # Сохраняем контекст в Redis (Бизнес-логика сохранена!)
                context_manager = RedisContextManager()
                fallback_key = f"fallback_attributes:{user_id}"
                await context_manager.set_context(fallback_key, attributes)
                # Redis client нужен для expire, получаем его из менеджера
                if context_manager.redis_client:
                    await context_manager.redis_client.expire(fallback_key, 600)
                
                # Группируем кнопки по 2 в ряд
                buttons_grid = [fallback_options[i:i+2] for i in range(0, len(fallback_options), 2)]
                
                return [CoreResponse(
                    type="clarification", 
                    content=f"🖼️ Точных фото нет. Попробуем упростить?",
                    buttons=buttons_grid
                )]
            
            # Если картинки найдены
            images = api_data.get("images", [])
            used_objects = api_data.get("used_objects", [])
            
            # Формируем ответ с картинками
            for index, img in enumerate(images[:5]):
                if isinstance(img, dict) and "image_path" in img:
                    responses.append(CoreResponse(
                        type="image", 
                        content=img["image_path"],
                        # Прикрепляем контекст только к первому сообщению
                        used_objects=used_objects if index == 0 else []
                    ))
            
            if not responses:
                return [CoreResponse(type="text", content=f"Ошибка: данные изображений некорректны.")]
            
            return responses

    except Exception as e:
        logger.error(f"Ошибка в handle_get_picture: {e}", exc_info=True)
        await log_critical(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Произошла внутренняя ошибка при поиске изображений.")]


async def handle_get_description(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str, 
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    offset = analysis.get("offset", 0)
    
    logger.info(f"[{user_id}] handle_get_description: объект='{object_nom}', offset={offset}")

    if not object_nom:
        return [CoreResponse(type="text", content="Не указан объект для поиска описания.")]
    
    if on_status:
        await on_status(f"🔍 Ищу описание для «{object_nom}»...")
        
    find_url = f"{API_URLS['find_species_with_description']}"
    payload = {"name": object_nom, "limit": 4, "offset": offset} 

    responses = []
    if debug_mode:
        responses.append(CoreResponse(type="debug", content=f"🐞 **API Request**\nURL: `{find_url}`\nPayload: `{payload}`"))

    try:
        async with session.post(find_url, json=payload, timeout=DEFAULT_TIMEOUT) as find_resp:
            if not find_resp.ok:
                await log_api_fail(session, user_id, find_url, find_resp.status, await find_resp.text(), original_query, context=analysis)
                return [CoreResponse(type="text", content=f"Ошибка API при поиске «{object_nom}».")]
            
            data = await find_resp.json()
            status = data.get("status")

            # СЛУЧАЙ 1: Неоднозначность (Ambiguous)
            if status == "ambiguous":
                matches = data.get("matches", [])
                
                # Сохраняем контекст пагинации в Redis (Бизнес-логика сохранена!)
                context_to_save = {
                    "options": matches,
                    "original_term": object_nom, 
                    "offset": offset             
                }
                context_manager = RedisContextManager()
                options_key = f"clarify_options:{user_id}"
                await context_manager.set_context(options_key, context_to_save)
                if context_manager.redis_client:
                    await context_manager.redis_client.expire(options_key, 300)

                # Формируем кнопки
                buttons = []
                for i, match_name in enumerate(matches):
                    buttons.append([{"text": match_name, "callback_data": f"clarify_idx:{i}"}])
                
                # Системные кнопки
                system_buttons = []
                if matches:
                    system_buttons.append({"text": "🎲 Любую", "callback_data": "clarify_idx:0"})
                if data.get("has_more", False):
                    system_buttons.append({"text": "🔍 Поискать еще", "callback_data": "clarify_more"})
                if system_buttons:
                    buttons.append(system_buttons)

                responses.append(CoreResponse(
                    type="clarification",
                    content=f"Я знаю несколько видов для «{object_nom}». Уточните, какой именно вас интересует?",
                    buttons=buttons
                ))
                return responses
            
            # СЛУЧАЙ 2: Найдено (Found)
            elif status == "found":
                canonical_name = data.get("matches", [object_nom])[0]
                stoplist_param = 1 if get_user_settings(user_id).get("stoplist_enabled", True) else 2
            
                desc_url = (f"{API_URLS['get_description']}?species_name={canonical_name}"
                            f"&debug_mode={str(debug_mode).lower()}"
                            f"&in_stoplist={stoplist_param}"
                            f"&query={original_query}")

                async with session.get(desc_url, timeout=DEFAULT_TIMEOUT) as desc_resp:
                    if desc_resp.ok:
                        api_data = await desc_resp.json()
                        descriptions = api_data.get("descriptions", [])
                        text = ""
                        
                        if descriptions:
                            first_item = descriptions[0]
                            text = first_item.get("content", "") if isinstance(first_item, dict) else str(first_item)
                        
                        if text:
                            responses.append(CoreResponse(
                                type="text", 
                                content=text,
                                used_objects=api_data.get("used_objects", [])
                            ))
                            return responses
                        
            await log_zero_results(
                session, original_query, user_id, 
                action="describe", 
                search_params={"object": object_nom}, 
                context=analysis
            )
            # СЛУЧАЙ 3: Не найдено (Not Found) - Пробуем GigaChat Fallback
            if get_user_fallback_setting(user_id):
                if on_status: await on_status("Обращаюсь к GigaChat...")
                fallback_answer = await call_gigachat_fallback_service(session, original_query)
                if fallback_answer: 
                    responses.append(CoreResponse(
                        type="text", 
                        content=f"**Ответ от GigaChat:**\n\n{fallback_answer}"
                    ))
                    return responses
            
            responses.append(CoreResponse(type="text", content=f"К сожалению, у меня нет описания для «{object_nom}»."))
            return responses

    except Exception as e:
        logger.error(f"Ошибка в handle_get_description: {e}", exc_info=True)
        await log_critical(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Проблема с подключением к серверу описаний.")]