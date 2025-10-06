# --- НАЧАЛО ФАЙЛА: logic/api_handlers.py ---

import aiohttp
import asyncio
import logging
import base64
import json
from typing import Dict, Any, List

from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from utils.settings_manager import get_user_settings

logger = logging.getLogger(__name__)

# --- Вспомогательные функции ---

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

# --- Обработчики API ---

async def handle_get_picture(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    logger.info(f"--- Запуск handle_get_picture с result: {result} ---")
    messages = []
    object_nom = result.get("object")
    features = result.get("features", {})
    
    url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
    payload = {"species_name": object_nom, "features": features}

    try:
        logger.info(f"API CALL: handle_get_picture. URL: {url}, Payload: {json.dumps(payload, ensure_ascii=False)}")
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            logger.info(f"API RESPONSE: handle_get_picture. Status: {resp.status}")
            raw_text = await resp.text()
            try:
                data = json.loads(raw_text)
                logger.info(f"API RESPONSE DATA (JSON): {data}")
            except json.JSONDecodeError as e:
                logger.error(f"Не удалось прочитать JSON из ответа API: {e}")
                logger.error(f"RAW RESPONSE TEXT: {raw_text}")
                return [{"type": "text", "content": f"Ошибка сервера: неверный формат ответа (status {resp.status})."}]

            # === ИЗМЕНЕНИЕ ЗДЕСЬ: Передаем user_id в fallback ===
            if not resp.ok or data.get("status") == "not_found" or not data.get("images"):
                # Проверяем, есть ли признаки для упрощения
                if len(features) >= 1:  # Если есть хотя бы один признак - предлагаем упрощение
                    # Получаем user_id из result (должен передаваться из gigachat_handler)
                    user_id = result.get("user_id", "unknown")
                    return await handle_picture_fallback(session, result, debug_mode, user_id)
                else:
                    return [{"type": "text", "content": f"Извините, я не нашел изображений для '{object_nom}'."}]

            images = data.get("images", [])
            sent_images_count = 0
            for img in images[:5]:
                if isinstance(img, dict) and "image_path" in img:
                    image_url = img["image_path"]
                    try:
                        async with session.head(image_url, timeout=5, allow_redirects=True) as check_resp:
                            if check_resp.status == 200:
                                messages.append({"type": "image", "content": image_url})
                                sent_images_count += 1
                    except Exception as e:
                        logger.warning(f"Не удалось проверить URL изображения {image_url}: {e}")
            
            if sent_images_count == 0:
                 messages.append({"type": "text", "content": f"Извините, не удалось загрузить ни одного изображения для '{object_nom}'."})

    except asyncio.TimeoutError:
        logger.error("API CALL TIMEOUT: Запрос к /search_images_by_features превысил таймаут.")
        return [{"type": "text", "content": "Сервер изображений не отвечает. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_picture: {e}", exc_info=True)
        messages.append({"type": "text", "content": "Произошла внутренняя ошибка при поиске изображений."})

    logger.info(f"--- Завершение handle_get_picture. Отправляется {len(messages)} сообщений. ---")
    return messages

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

async def handle_picture_fallback(session: aiohttp.ClientSession, result: dict, debug_mode: bool, user_id: str) -> list:
    """
    Создает варианты упрощения, предварительно проверяя их в API
    """
    object_nom = result.get("object")
    original_features = result.get("features", {})
    
    logger.info(f"Обработка fallback для {object_nom} с features: {original_features}")
    
    fallback_options = []
    
    # Проверяем каждый возможный вариант упрощения
    if original_features.get("season"):
        # Вариант 1: Без сезона
        test_features = original_features.copy()
        test_features.pop("season")
        if await check_simplified_search(session, object_nom, test_features, debug_mode):
            fallback_options.append({
                "text": f"❄️ Без сезона",
                "callback_data": f"fallback:no_season:{object_nom}",
                "features": test_features
            })
    
    if original_features.get("habitat"):
        # Вариант 2: Без места обитания
        test_features = original_features.copy()
        test_features.pop("habitat")
        if await check_simplified_search(session, object_nom, test_features, debug_mode):
            fallback_options.append({
                "text": f"🌲 Без места", 
                "callback_data": f"fallback:no_habitat:{object_nom}",
                "features": test_features
            })
    
    # Вариант 3: Только основной объект
    if len(original_features) >= 1:
        test_features = {}
        if await check_simplified_search(session, object_nom, test_features, debug_mode):
            fallback_options.append({
                "text": f"🖼️ Только объект",
                "callback_data": f"fallback:basic:{object_nom}",
                "features": test_features
            })
    
    # Если нет рабочих вариантов упрощения
    if not fallback_options:
        logger.info(f"Не найдено рабочих упрощений для {object_nom}")
        return [{"type": "text", "content": f"Извините, не нашел изображений для '{object_nom}' с любыми комбинациями признаков."}]
    
    # Сохраняем исходные features в Redis для этого пользователя
    from utils.context_manager import RedisContextManager
    context_manager = RedisContextManager()
    fallback_key = f"fallback_features:{user_id}"
    await context_manager.set_context(fallback_key, original_features)
    
    # Устанавливаем TTL 10 минут на случай если пользователь не нажмет кнопку
    if context_manager.redis_client:
        await context_manager.redis_client.expire(fallback_key, 600)
    
    logger.info(f"Сохранили fallback features для {user_id}: {original_features}")
    
    # Создаем кнопки
    buttons = []
    for i in range(0, len(fallback_options), 2):
        row = fallback_options[i:i+2]
        buttons.append([
            {"text": btn["text"], "callback_data": btn["callback_data"]} 
            for btn in row
        ])
    
    # Формируем текст
    feature_parts = []
    if original_features.get("season"):
        feature_parts.append(f"сезон «{original_features['season']}»")
    if original_features.get("habitat"):
        feature_parts.append(f"место «{original_features['habitat']}»")
    if original_features.get("flowering"):
        feature_parts.append("цветение")

    # Красивое соединение: "сезон «Лето» и место «Луг»"
    if len(feature_parts) == 1:
        features_text = feature_parts[0]
    elif len(feature_parts) == 2:
        features_text = f"{feature_parts[0]} и {feature_parts[1]}"
    else:
        features_text = ", ".join(feature_parts[:-1]) + f" и {feature_parts[-1]}"

    return [{
        "type": "clarification", 
        "content": f"🖼️ К сожалению, у меня нет фотографий {object_nom} сразу с {features_text}.\n\nДавайте попробуем упростить запрос? Вот что я нашел:",
        "buttons": buttons
    }]

async def handle_get_description(session: aiohttp.ClientSession, result: dict, user_id: str, original_query: str, debug_mode: bool, offset: int = 0) -> list:
    object_nom = result.get("object")
    
    find_url = f"{API_URLS['find_species_with_description']}"
    payload = {"name": object_nom, "limit": 4, "offset": offset}

    try:
        async with session.post(find_url, json=payload, timeout=DEFAULT_TIMEOUT) as find_resp:
            if not find_resp.ok:
                return [{"type": "text", "content": f"Извините, произошла ошибка при поиске '{object_nom}'."}]
            
            data = await find_resp.json()
            status = data.get("status")

            if status == "ambiguous":
                matches = data.get("matches", [])
                buttons = [[{"text": match, "callback_data": f"clarify_object:{match}"}] for match in matches]
                system_buttons_row = []
                if matches:
                    system_buttons_row.append({"text": "Любую 🎲", "callback_data": f"clarify_object:{matches[0]}"})

                has_more = data.get("has_more", False)
                if has_more:
                    new_offset = offset + len(matches)
                    callback_str = f"clarify_more:{object_nom}:{new_offset}"
                    system_buttons_row.append({"text": "Поискать еще 🔍", "callback_data": callback_str})

                if system_buttons_row:
                    buttons.append(system_buttons_row)

                return [{
                    "type": "clarification",
                    "content": f"Я знаю несколько видов для '{object_nom}'. Уточните, какой именно вас интересует?",
                    "buttons": buttons
                }]

            elif status == "found":
                canonical_name = data.get("matches", [object_nom])[0]
                logger.info(f"Найдено точное совпадение: '{canonical_name}'. Запрашиваю описание...")

                desc_url = f"{API_URLS['get_description']}?species_name={canonical_name}&debug_mode={str(debug_mode).lower()}"
                async with session.get(desc_url, timeout=DEFAULT_TIMEOUT) as desc_resp:
                    if not desc_resp.ok:
                         return [{"type": "text", "content": f"Нашел объект '{canonical_name}', но не смог загрузить его описание."}]
                    
                    desc_data = await desc_resp.json()
                    descriptions = desc_data.get("descriptions", [])
                    text = ""
                    if descriptions:
                        first_item = descriptions[0]
                        if isinstance(first_item, dict): text = first_item.get("content", "")
                        elif isinstance(first_item, str): text = first_item
                    
                    if text:
                        return [{"type": "text", "content": text, "canonical_name": canonical_name}]
            
            logger.warning(f"Описание для '{object_nom}' не найдено ни на одном из этапов.")
            if get_user_fallback_setting(user_id):
                fallback_answer = await call_gigachat_fallback_service(session, original_query)
                if fallback_answer: return [{"type": "text", "content": f"**Ответ от GigaChat:**\n\n{fallback_answer}", "parse_mode": "Markdown"}]
            
            return [{"type": "text", "content": f"Извините, описание для '{object_nom}' не найдено."}]

    except Exception as e:
        logger.error(f"Ошибка в handle_get_description: {e}", exc_info=True)
        return [{"type": "text", "content": "Проблема с подключением к серверу описаний."}]

async def handle_comparison(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    object1 = result.get("object1")
    object2 = result.get("object2")
    if not object1 or not object2:
        return [{"type": "text", "content": "Недостаточно данных для сравнения."}]

    prompt = f"Сравни два объекта: '{object1}' и '{object2}'. Ответ дай СТРОГО в виде списка с буллитами (•). Не используй заголовки или вступления. Начни сразу с первого пункта сравнения."
    comparison_text = await call_gigachat_fallback_service(session, prompt)

    if comparison_text:
        full_answer = f"Отлично! Вот основные отличия между **{object1}** и **{object2}**:\n\n{comparison_text}"
        return [{"type": "text", "content": full_answer, "parse_mode": "Markdown"}]
    else:
        return [{"type": "text", "content": "Извините, не удалось сгенерировать сравнение."}]

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, object_name: str, debug_mode: bool, geo_name: str = None) -> list:
    messages = []
    full_url = f"{url}?debug_mode={str(debug_mode).lower()}"
    
    async with session.post(full_url, json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        map_data = await map_resp.json()

        if not map_resp.ok:
            return [{"type": "text", "content": "Не удалось построить карту."}]

        names = map_data.get("names", [])
        unique_names = sorted(list(set(name.capitalize() for name in names)))
        
        caption_text = ""
        if unique_names:
            text = (f"📍 Рядом с '{geo_name}' вы можете встретить '{object_name}' в местах:\n" if geo_name 
                    else f"📍 '{object_name.capitalize()}' встречается в местах:\n")
            caption_text = text + "• " + "\n• ".join(unique_names)
        
        if map_data.get("status") == "no_objects":
            text = (f"К сожалению, я не нашел '{object_name}' поблизости от '{geo_name}'." if geo_name
                    else f"К сожалению, я не смог найти ареал обитания для '{object_name}'.")
            messages.append({"type": "text", "content": text})
            
        if map_data.get("interactive_map") and map_data.get("static_map"):
            messages.append({"type": "map", "static": map_data["static_map"], "interactive": map_data["interactive_map"], "caption": caption_text or f"Карта для: {object_name}"})
        elif caption_text:
            messages.append({"type": "text", "content": caption_text})

        return messages

async def handle_nearest(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    object_nom = result.get("object")
    geo_nom = result.get("geo_place")
    try:
        coords_url = API_URLS["get_coords"]
        async with session.post(coords_url, json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok or (await resp.json()).get("status") == "not_found":
                return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
            coords = await resp.json()

        payload = {"latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 35, "species_name": object_nom, "object_type": "geographical_entity"}
        return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode, geo_nom)
    except Exception as e:
        logger.error(f"Ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске ближайших мест."}]

async def handle_draw_locate_map(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    object_nom = result.get("object")
    payload = {"latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, "species_name": object_nom, "object_type": "geographical_entity"}
    try:
        return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode)
    except Exception as e:
        logger.error(f"Ошибка в handle_draw_locate_map: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при построении карты ареала."}]

async def handle_objects_in_polygon(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    geo_nom = result.get("geo_place")
    
    # Существующий запрос к API
    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {"name": geo_nom, "buffer_radius_km": 5}
    
    async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
        if not resp.ok:
            return [{"type": "text", "content": f"Не удалось найти полигон для '{geo_nom}'."}]
        
        data = await resp.json()
        objects_list = data.get("all_biological_names", [])
        
        messages = []
        
        # 1. Показываем карту (как сейчас)
        if data.get("interactive_map") and data.get("static_map"):
            caption = f"📍 Объекты в районе: {geo_nom}"
            if objects_list:
                caption += f"\n\nНайдено объектов: {len(objects_list)}"
            
            messages.append({
                "type": "map", 
                "static": data["static_map"], 
                "interactive": data["interactive_map"], 
                "caption": caption
            })
        
        # 2. Если много объектов - предлагаем УМНЫЙ обзор через LLM
        if len(objects_list) > 3:
            # НОВОЕ: вместо create_exploration_offer сразу показываем умный обзор
            overview_msg = await create_llm_overview(geo_nom, objects_list)
            messages.append(overview_msg)
        # 3. Если мало - показываем простой список
        elif objects_list:
            simple_list = f"🌿 В районе **{geo_nom}** найдены:\n• " + "\n• ".join(objects_list)
            messages.append({"type": "text", "content": simple_list})
        else:
            messages.append({"type": "text", "content": f"В районе '{geo_nom}' не найдено известных объектов."})
        
        return messages

async def create_llm_overview(geo_place: str, objects_list: list) -> dict:
    """
    Создает предложение увидеть умный обзор (БЕЗ вызова LLM здесь)
    """
    buttons = [
        [{"text": "🎯 Умный обзор", "callback_data": f"explore:overview:{geo_place}"}],
        [{"text": "📋 Все объекты", "callback_data": f"explore:full_list:{geo_place}"}]
    ]
    
    return {
        "type": "clarification",
        "content": f"🗺️ **{geo_place}**\n\nНашел {len(objects_list)} объектов. Хотите увидеть умный обзор с анализом или просто список?",
        "buttons": buttons
    }

# --- Главный маршрутизатор ---

async def handle_intent(session: aiohttp.ClientSession, intent: str, result: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    handler_kwargs: Dict[str, Any] = {"session": session, "result": result, "debug_mode": debug_mode}
    
    if intent == "get_text":
        handler_kwargs.update({"user_id": user_id, "original_query": original_query})
        if "offset" in result:
            handler_kwargs["offset"] = result["offset"]

    handlers = {
        "get_picture": handle_get_picture,
        "get_text": handle_get_description,
        "get_intersection_object_on_map": handle_nearest,
        "get_location": handle_draw_locate_map,
        "get_objects_in_polygon": handle_objects_in_polygon,
        "get_comparison": handle_comparison
    }

    handler_func = handlers.get(intent)
    if handler_func:
        import inspect
        sig = inspect.signature(handler_func)
        filtered_kwargs = {k: v for k, v in handler_kwargs.items() if k in sig.parameters}
        
        return await handler_func(**filtered_kwargs)
    else:
        logger.warning(f"Неизвестный intent: {intent}")
        return [{"type": "text", "content": "Извините, я пока не умею обрабатывать такой запрос."}]
        
# --- КОНЕЦ ФАЙЛА: logic/api_handlers.py ---