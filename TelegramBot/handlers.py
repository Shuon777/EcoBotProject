import aiohttp
import logging
import json
from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from settings_manager import get_user_settings

logger = logging.getLogger(__name__)

def get_user_fallback_setting(user_id: str) -> bool:
    """Проверяет, включен ли fallback для пользователя."""
    return get_user_settings(user_id).get("gigachat_fallback", False)

async def call_gigachat_fallback_service(question: str) -> str | None:
    """Асинхронно делает HTTP-запрос к внешнему GigaChat сервису."""
    url = GIGACHAT_FALLBACK_URL
    logger.debug(f"Обращение к GigaChat Fallback API: {url}")
    try:
        payload = {"question": question}
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=GIGACHAT_TIMEOUT) as response:
                if response.ok:
                    logger.info("Fallback-сервис GigaChat ответил успешно.")
                    data = await response.json()
                    return data.get("answer")
                else:
                    logger.error(f"Fallback-сервис GigaChat вернул ошибку: {response.status} {await response.text()}")
                    return None
    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка при подключении к fallback-сервису GigaChat: {e}")
        return None

async def handle_get_picture(result: dict) -> list:
    messages = []
    object_nom = result.get("object")
    features = result.get("features", {})
    url = API_URLS["search_images"]

    try:
        payload = {"species_name": object_nom}
        if features:
            payload["features"] = features
        
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                if not resp.ok:
                    logger.warning(f"API {url} вернуло ошибку {resp.status} для '{object_nom}'")
                    return [{"type": "text", "content": f"Извините, изображения для '{object_nom}' не найдены."}]
                
                data = await resp.json()
                if data.get("status") == "not_found" or not data.get("images"):
                    return [{"type": "text", "content": f"Извините, ничего не найдено для '{object_nom}'."}]

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
                                else:
                                    logger.warning(f"URL изображения вернул статус {check_resp.status}: {image_url}")
                        except aiohttp.ClientError as e:
                            logger.warning(f"Не удалось проверить URL изображения {image_url}: {e}")
                
                if sent_images_count == 0:
                     messages.append({"type": "text", "content": f"Извините, не удалось загрузить ни одного изображения для '{object_nom}'."})

    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка в handle_get_picture: {e}")
        messages.append({"type": "text", "content": "Проблема с подключением к серверу. Попробуйте позже."})
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_picture: {e}", exc_info=True)
        messages.append({"type": "text", "content": "Произошла внутренняя ошибка."})

    return messages

async def handle_get_description(result: dict, user_id: str, original_query: str) -> list:
    object_nom = result.get("object")
    url = f"{API_URLS['get_description']}?species_name={object_nom}"

    try:
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Обращение к API: {url}")
            async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
                data = await resp.json() if resp.ok else {}
                descriptions = data.get("descriptions", [])
                
                text = ""
                # --- ИЗМЕНЕНИЕ: БЕРЕМ ТОЛЬКО ПЕРВОЕ ОПИСАНИЕ ---
                if descriptions:
                    first_item = descriptions[0]
                    if isinstance(first_item, dict):
                        text = first_item.get("content", "")
                    elif isinstance(first_item, str):
                        text = first_item
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                # Если после попытки взять первое описание текста все равно нет,
                # или ответ API был неуспешным, запускаем fallback.
                if not resp.ok or not text:
                    if get_user_fallback_setting(user_id):
                        fallback_answer = await call_gigachat_fallback_service(original_query)
                        if fallback_answer:
                            full_answer = f"**Ответ от GigaChat:**\n\n{fallback_answer}"
                            return [{"type": "text", "content": full_answer, "parse_mode": "Markdown"}]
                        else:
                            return [{"type": "text", "content": "Извините, не удалось получить дополнительную информацию."}]
                    else:
                        logger.warning(f"API {url} вернуло ошибку {resp.status} или пустое описание.")
                        return [{"type": "text", "content": f"Извините, описание для '{object_nom}' не найдено."}]

                return [{"type": "text", "content": text}]
        
    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка в handle_get_description: {e}")
        return [{"type": "text", "content": "Проблема с подключением к серверу. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_description: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, object_name: str, geo_name: str = None) -> list:
    """Асинхронная вспомогательная функция для получения карт."""
    messages = []
    logger.debug(f"Обращение к API карт: {url} с телом: {payload}")
    async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        if not map_resp.ok:
            logger.error(f"API карт {url} вернуло ошибку {map_resp.status}: {await map_resp.text()}")
            return [{"type": "text", "content": "Не удалось построить карту."}]

        map_data = await map_resp.json()
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

async def handle_nearest(result: dict) -> list:
    object_nom = result.get("object")
    geo_nom = result.get("geo_place")

    try:
        async with aiohttp.ClientSession() as session:
            coords_url = API_URLS["get_coords"]
            logger.debug(f"Обращение к API координат: {coords_url} с телом: {{'name': '{geo_nom}'}}")
            async with session.post(coords_url, json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
                if not resp.ok or (await resp.json()).get("status") == "not_found":
                    return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
                coords = await resp.json()

            payload = {
                "latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 35, 
                "species_name": object_nom, "object_type": "geographical_entity"
            }
            return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, geo_nom)
            
    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка в handle_nearest: {e}")
        return [{"type": "text", "content": "Проблема с подключением к серверу. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_draw_locate_map(result: dict) -> list:
    object_nom = result.get("object")
    payload = {
        "latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, 
        "species_name": object_nom, "object_type": "geographical_entity"
    }
    try:
        async with aiohttp.ClientSession() as session:
            return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom)
    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка в handle_draw_locate_map: {e}")
        return [{"type": "text", "content": "Проблема с подключением к серверу. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_draw_locate_map: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_objects_in_polygon(result: dict) -> list:
    geo_nom = result.get("geo_place")
    url = API_URLS["objects_in_polygon"]
    payload = {"name": geo_nom, "buffer_radius_km": 5, "object_type": "biological_entity"}
    messages = []
    
    try:
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                if not resp.ok:
                    return [{"type": "text", "content": f"Не удалось найти полигон для '{geo_nom}'. Пожалуйста, уточните название."}]

                data = await resp.json()
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

    except aiohttp.ClientError as e:
        logger.error(f"Сетевая ошибка в handle_objects_in_polygon: {e}")
        return [{"type": "text", "content": "Проблема с подключением к серверу. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_objects_in_polygon: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка."}]

async def handle_intent(result: dict, user_id: str, original_query: str) -> list:
    intent = result.get("intent")
    
    # Обработчики теперь асинхронные
    handlers = {
        "get_picture": handle_get_picture,
        "get_text": lambda res: handle_get_description(res, user_id, original_query),
        "get_intersection_object_on_map": handle_nearest,
        "get_location": handle_draw_locate_map,
        "get_objects_in_polygon": handle_objects_in_polygon,
    }

    handler = handlers.get(intent)
    if handler:
        return await handler(result)
    else:
        logger.warning(f"Неизвестный intent: {intent}")
        return [{"type": "text", "content": "Извините, я пока не умею обрабатывать такой запрос."}]