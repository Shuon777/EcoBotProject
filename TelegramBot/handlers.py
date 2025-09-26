# --- НАЧАЛО ФАЙЛА TelegramBot/handlers.py ---
import aiohttp
import logging
from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from settings_manager import get_user_settings

logger = logging.getLogger(__name__)

def get_user_fallback_setting(user_id: str) -> bool:
    """Проверяет, включен ли fallback для пользователя."""
    return get_user_settings(user_id).get("gigachat_fallback", False)

async def call_gigachat_fallback_service(question: str) -> str | None:
    """Асинхронно делает HTTP-запрос к внешнему GigaChat сервису."""
    url = GIGACHAT_FALLBACK_URL
    try:
        payload = {"question": question}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=GIGACHAT_TIMEOUT) as response:
                if response.ok:
                    data = await response.json()
                    return data.get("answer")
                else:
                    logger.error(f"Fallback-сервис GigaChat вернул ошибку: {response.status} {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка при подключении к fallback-сервису GigaChat: {e}")
        return None

async def handle_get_picture(result: dict, debug_mode: bool) -> list:
    messages = []
    object_nom = result.get("object")
    features = result.get("features", {})
    
    # --- ИЗМЕНЕНИЕ: debug_mode теперь в URL ---
    url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
    payload = {"species_name": object_nom, "features": features}

    try:
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                data = await resp.json()

                # --- ИЗМЕНЕНИЕ: Разное поведение в зависимости от debug_mode ---
                if debug_mode:
                    # В режиме отладки возвращаем весь JSON как есть
                    return [data]

                if not resp.ok or data.get("status") == "not_found" or not data.get("images"):
                    return [{"type": "text", "content": f"Извините, изображения для '{object_nom}' не найдены."}]

                # В обычном режиме - извлекаем только картинки
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
                        except aiohttp.ClientError as e:
                            logger.warning(f"Не удалось проверить URL изображения {image_url}: {e}")
                
                if sent_images_count == 0:
                     messages.append({"type": "text", "content": f"Извините, не удалось загрузить ни одного изображения для '{object_nom}'."})

    except aiohttp.ClientError as e:
        messages.append({"type": "text", "content": "Проблема с подключением к серверу."})
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_picture: {e}", exc_info=True)
        messages.append({"type": "text", "content": "Произошла внутренняя ошибка."})

    return messages

async def handle_get_description(result: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    messages = []
    object_nom = result.get("object")
    url = f"{API_URLS['get_description']}?species_name={object_nom}&debug_mode={str(debug_mode).lower()}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
                data = await resp.json() if resp.ok else {}
                
                if debug_mode and data.get("debug"):
                    messages.append({"type": "debug", "content": data["debug"]})

                descriptions = data.get("descriptions", [])
                text = ""
                if descriptions:
                    first_item = descriptions[0]
                    if isinstance(first_item, dict): text = first_item.get("content", "")
                    elif isinstance(first_item, str): text = first_item

                if not resp.ok or not text:
                    if get_user_fallback_setting(user_id):
                        fallback_answer = await call_gigachat_fallback_service(original_query)
                        if fallback_answer: messages.append({"type": "text", "content": f"**Ответ от GigaChat:**\n\n{fallback_answer}", "parse_mode": "Markdown"})
                        else: messages.append({"type": "text", "content": "Извините, не удалось получить дополнительную информацию."})
                    else: messages.append({"type": "text", "content": f"Извините, описание для '{object_nom}' не найдено."})
                    return messages

                messages.append({"type": "text", "content": text})
                return messages
        
    except aiohttp.ClientError as e:
        return [{"type": "text", "content": "Проблема с подключением к серверу."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_description: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, object_name: str, debug_mode: bool, geo_name: str = None) -> list:
    messages = []
    full_url = f"{url}?debug_mode={str(debug_mode).lower()}"
    logger.debug(f"Обращение к API карт: {full_url} с телом: {payload}")
    
    async with session.post(full_url, json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        map_data = await map_resp.json()

        if debug_mode and map_data.get("debug"):
            messages.append({"type": "debug", "content": map_data["debug"]})

        if not map_resp.ok:
            messages.append({"type": "text", "content": "Не удалось построить карту."})
            return messages

        names = map_data.get("names", [])
        unique_names = sorted(list(set(name.capitalize() for name in names)))
        
        if unique_names:
            text = (f"📍 Рядом с '{geo_name}' вы можете встретить '{object_name}' в местах:\n" if geo_name 
                    else f"📍 '{object_name.capitalize()}' встречается в местах:\n")
            message_text = text + "• " + "\n• ".join(unique_names)
            messages.append({"type": "text", "content": message_text})
        
        if map_data.get("status") == "no_objects":
            text = (f"К сожалению, я не нашел '{object_name}' поблизости от '{geo_name}'." if geo_name
                    else f"К сожалению, я не смог найти ареал обитания для '{object_name}'.")
            messages.append({"type": "text", "content": text})
            
        if map_data.get("interactive_map") and map_data.get("static_map"):
            messages.append({"type": "map", "static": map_data["static_map"], "interactive": map_data["interactive_map"], "caption": f"Места обитания: {object_name}"})
        
        return messages

async def handle_nearest(result: dict, debug_mode: bool) -> list:
    object_nom = result.get("object")
    geo_nom = result.get("geo_place")

    try:
        async with aiohttp.ClientSession() as session:
            coords_url = API_URLS["get_coords"]
            async with session.post(coords_url, json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
                if not resp.ok or (await resp.json()).get("status") == "not_found":
                    return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
                coords = await resp.json()

            payload = {
                "latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 35, 
                "species_name": object_nom, "object_type": "geographical_entity"
            }
            return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode, geo_nom)
            
    except aiohttp.ClientError as e: return [{"type": "text", "content": "Проблема с подключением к серверу."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_draw_locate_map(result: dict, debug_mode: bool) -> list:
    object_nom = result.get("object")
    payload = {
        "latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, 
        "species_name": object_nom, "object_type": "geographical_entity"
    }
    try:
        async with aiohttp.ClientSession() as session:
            return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode)
    except aiohttp.ClientError as e: return [{"type": "text", "content": "Проблема с подключением к серверу."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_draw_locate_map: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_objects_in_polygon(result: dict, debug_mode: bool) -> list:
    messages = []
    geo_nom = result.get("geo_place")
    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {"name": geo_nom, "buffer_radius_km": 5, "object_type": "biological_entity"}
    
    try:
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                data = await resp.json()

                if debug_mode and data.get("debug"):
                    messages.append({"type": "debug", "content": data["debug"]})

                if not resp.ok:
                    messages.append({"type": "text", "content": f"Не удалось найти полигон для '{geo_nom}'."})
                    return messages

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

    except aiohttp.ClientError as e: return [{"type": "text", "content": "Проблема с подключением к серверу."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_objects_in_polygon: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_intent(result: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    intent = result.get("intent")
    
    handler_kwargs = {"result": result, "debug_mode": debug_mode}
    if intent == "get_text":
        handler_kwargs.update({"user_id": user_id, "original_query": original_query})

    handlers = {
        "get_picture": handle_get_picture,
        "get_text": handle_get_description,
        "get_intersection_object_on_map": handle_nearest,
        "get_location": handle_draw_locate_map,
        "get_objects_in_polygon": handle_objects_in_polygon,
    }

    handler_func = handlers.get(intent)
    if handler_func:
        if intent != "get_text":
            handler_kwargs.pop("user_id", None)
            handler_kwargs.pop("original_query", None)
        return await handler_func(**handler_kwargs)
    else:
        logger.warning(f"Неизвестный intent: {intent}")
        return [{"type": "text", "content": "Извините, я пока не умею обрабатывать такой запрос."}]
# --- КОНЕЦ ФАЙЛА TelegramBot/handlers.py ---