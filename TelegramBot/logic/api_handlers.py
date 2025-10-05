# --- НАЧАЛО ПОЛНОГО ФАЙЛА: logic/api_handlers.py ---

import aiohttp
import asyncio
import logging
import json
from typing import Dict, Any, List

# Исправляем импорты для новой структуры
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

            if not resp.ok or data.get("status") == "not_found" or not data.get("images"):
                return [{"type": "text", "content": f"Извините, я не нашел изображений для '{object_nom}' с такими признаками."}]

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

async def handle_get_description(session: aiohttp.ClientSession, result: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    object_nom = result.get("object")
    url = f"{API_URLS['get_description']}?species_name={object_nom}&debug_mode={str(debug_mode).lower()}"

    try:
        async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                return [{"type": "text", "content": f"Извините, не удалось получить описание для '{object_nom}'."}]
            data = await resp.json()
            
            status = data.get("status")
            if status == "ambiguous":
                matches = data.get("matches", [])
                buttons = [[{"text": match, "callback_data": f"clarify_object:{match}"}] for match in matches[:5]]
                return [{
                    "type": "buttons",
                    "content": f"Я знаю несколько видов для '{object_nom}'. Уточните, какой именно вас интересует?",
                    "buttons": buttons
                }]

            descriptions = data.get("descriptions", [])
            text = ""
            if descriptions:
                first_item = descriptions[0]
                if isinstance(first_item, dict): text = first_item.get("content", "")
                elif isinstance(first_item, str): text = first_item

            if not text:
                if get_user_fallback_setting(user_id):
                    fallback_answer = await call_gigachat_fallback_service(session, original_query)
                    if fallback_answer: return [{"type": "text", "content": f"**Ответ от GigaChat:**\n\n{fallback_answer}", "parse_mode": "Markdown"}]
                return [{"type": "text", "content": f"Извините, описание для '{object_nom}' не найдено."}]

            return [{"type": "text", "content": text}]
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

        payload = {"latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 35, "species_name": object_nom}
        return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode, geo_nom)
    except Exception as e:
        logger.error(f"Ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске ближайших мест."}]

async def handle_draw_locate_map(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    object_nom = result.get("object")
    payload = {"latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, "species_name": object_nom}
    try:
        return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode)
    except Exception as e:
        logger.error(f"Ошибка в handle_draw_locate_map: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при построении карты ареала."}]

async def handle_objects_in_polygon(session: aiohttp.ClientSession, result: dict, debug_mode: bool) -> list:
    geo_nom = result.get("geo_place")
    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {"name": geo_nom, "buffer_radius_km": 5}
    
    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                return [{"type": "text", "content": f"Не удалось найти полигон для '{geo_nom}'."}]
            data = await resp.json()
            
            messages = []
            names = data.get("all_biological_names", [])
            if names:
                unique_names = sorted(list(set(name.capitalize() for name in names)))
                flora_list = f"🌿 В районе '{geo_nom}' найдены следующие объекты:\n" + "• " + "\n• ".join(unique_names)
                messages.append({"type": "text", "content": flora_list})
            else:
                messages.append({"type": "text", "content": f"В районе '{geo_nom}' не найдено известных мне объектов."})

            if data.get("interactive_map") and data.get("static_map"):
                messages.append({"type": "map", "static": data["static_map"], "interactive": data["interactive_map"], "caption": f"Объекты в районе: {geo_nom}"})
            
            return messages
    except Exception as e:
        logger.error(f"Ошибка в handle_objects_in_polygon: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске объектов."}]


# --- Главный маршрутизатор ---

async def handle_intent(session: aiohttp.ClientSession, intent: str, result: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    handler_kwargs: Dict[str, Any] = {"session": session, "result": result, "debug_mode": debug_mode}
    
    if intent == "get_text":
        handler_kwargs.update({"user_id": user_id, "original_query": original_query})

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