import aiohttp
import logging
import time
import asyncio
from typing import Dict, Any
from urllib.parse import quote
from config import API_URLS, DEFAULT_TIMEOUT, STAND_SECRET_KEY, STAND_SESSION_TIMEOUT
from utils.settings_manager import get_user_settings, update_user_settings
from logic.entity_normalizer_for_maps import normalize_entity_name_for_maps, ENTITY_MAP
from logic.entity_normalizer import normalize_entity_name, GROUP_ENTITY_MAP, should_include_object_name
from logic.baikal_context import determine_baikal_relation

logger = logging.getLogger(__name__)

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, object_name: str, debug_mode: bool, geo_name: str = None) -> list:
    async with session.post(f"{url}?debug_mode={str(debug_mode).lower()}", json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        map_data = await map_resp.json()
        if not map_resp.ok: return [{"type": "text", "content": "Не удалось построить карту."}]

        names = sorted(list(set(name.capitalize() for name in map_data.get("names", []))))
        caption = ""
        if names:
            text_header = f"📍 Рядом с '{geo_name}' вы можете встретить '{object_name}' в местах:\n" if geo_name else f"📍 '{object_name.capitalize()}' встречается в местах:\n"
            caption = text_header + "• " + "\n• ".join(names)

        messages = []
        if map_data.get("status") == "no_objects":
            text = f"К сожалению, я не нашел '{object_name}'" + (f" поблизости от '{geo_name}'." if geo_name else " на карте.")
            messages.append({"type": "text", "content": text})

        if map_data.get("interactive_map") and map_data.get("static_map"):
            s = map_data["static_map"]
            i = map_data["interactive_map"]
            logger.info(f"static_map - {s} and interactive_map - {i}")
            messages.append({"type": "map", "static": map_data["static_map"], "interactive": map_data["interactive_map"], "caption": caption})
        elif caption:
            messages.append({"type": "text", "content": caption})
        return messages

async def handle_nearest(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    object_nom = analysis.get("primary_entity", {}).get("name")
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    if not object_nom or not geo_nom:
        return [{"type": "text", "content": "Недостаточно данных для поиска: нужен и объект, и место."}]

    try:
        logger.info(f"Обращение к get_coords с payload - name: {geo_nom}")
        async with session.post(API_URLS["get_coords"], json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok: return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
            coords = await resp.json()
        logger.info(f"Ответ от get_coords - {coords}")
        payload = {"latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 50, "species_name": object_nom, "object_type": "geographical_entity"}
        return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode, geo_nom)
    except Exception as e:
        logger.error(f"Ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске ближайших мест."}]

async def handle_draw_locate_map(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    # [НОВОЕ] Извлекаем данные из `analysis`
    object_nom = analysis.get("primary_entity", {}).get("name")
    if not object_nom: return [{"type": "text", "content": "Не указан объект для отображения на карте."}]
    
    payload = {"latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, "species_name": object_nom, "object_type": "geographical_entity"}
    return await _get_map_from_api(session, API_URLS["coords_to_map"], payload, object_nom, debug_mode)

async def handle_draw_map_of_infrastructure(session: aiohttp.ClientSession, analysis: dict, user_id: str, debug_mode: bool) -> list:
    """
    Обрабатывает запросы на отображение инфраструктуры на карте.
    - Основная логика: вызывает API бэкенда, получает данные и формирует ответ для пользователя (карту или текст).
    - Дополнительная логика: если пользователь пришел со стенда (флаг on_stand), извлекает external_id из ответа 
      основного API и отправляет их на отдельный эндпоинт стенда.
    """
    primary_entity = analysis.get("primary_entity") or {}
    secondary_entity = analysis.get("secondary_entity") or {}

    raw_object_name = primary_entity.get("name")
    area_name = secondary_entity.get("name", "")

    if not raw_object_name:
        return [{"type": "text", "content": "Не смог определить, что нужно найти на карте."}]

    normalized_type = normalize_entity_name_for_maps(raw_object_name)
    is_specific_name_search = normalized_type not in ENTITY_MAP.values()

    payload = {"limit": 10}
    if is_specific_name_search:
        payload["object_name"] = raw_object_name
        if area_name:
            payload["area_name"] = area_name
        logger.info(f"Режим поиска: по имени. Payload: {payload}")
    else:
        payload["object_type"] = normalized_type
        if not area_name:
             return [{"type": "text", "content": f"Пожалуйста, уточните, где вы хотите найти '{raw_object_name}'?"}]
        payload["area_name"] = area_name
        logger.info(f"Режим поиска: по типу. Payload: {payload}")

    try:
        url = f"{API_URLS['show_map_infrastructure']}?debug_mode={str(debug_mode).lower()}"
        logger.info(f"Запрос к API инфраструктуры: {url} с payload: {payload}")
        
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            content_type = resp.headers.get('Content-Type', '').lower()
            
            if 'application/json' not in content_type:
                logger.error(f"API инфраструктуры вернул не JSON. Status: {resp.status}, Content-Type: {content_type}")
                if resp.status == 404: return [{"type": "text", "content": f"Сервис поиска временно недоступен."}]
                elif resp.status == 500: return [{"type": "text", "content": "Внутренняя ошибка сервера инфраструктуры."}]
                else: return [{"type": "text", "content": "Сервер инфраструктуры вернул некорректный ответ."}]

            data = await resp.json()

            user_settings = get_user_settings(user_id)
            is_stand_session_active = False
            if user_settings.get("on_stand"):
                last_active_time = user_settings.get("stand_last_active", 0)
                time_elapsed = time.time() - last_active_time
            
                if time_elapsed < STAND_SESSION_TIMEOUT:
                    is_stand_session_active = True
                    update_user_settings(user_id, {"stand_last_active": time.time()})
                    logger.info(f"[{user_id}] Сессия 'у стенда' активна и продлена.")
                else:
                    update_user_settings(user_id, {"on_stand": False, "stand_last_active": None})
                    logger.info(f"[{user_id}] Сессия 'у стенда' истекла по таймауту. Флаг сброшен.")
            if is_stand_session_active:
                logger.info(f"[{user_id}] Пользователь со стенда. Запускаем дополнительную логику.")
                
                external_ids = []
                if "objects" in data and isinstance(data["objects"], list):
                    for obj in data["objects"]:
                        if isinstance(obj, dict) and obj.get("external_id"):
                            external_ids.append(obj["external_id"])

                if external_ids:
                    logger.info(f"[{user_id}] Найдено {len(external_ids)} external_id для отправки: {external_ids}")
                    stand_payload = {
                        "items": [{"id": ext_id} for ext_id in external_ids],
                        "secret_key": STAND_SECRET_KEY
                    }
                    
                    try:
                        stand_url = API_URLS['stand_endpoint']
                        async with session.post(stand_url, json=stand_payload, timeout=10) as stand_resp:
                            if stand_resp.ok:
                                logger.info(f"[{user_id}] Данные успешно отправлены на эндпоинт стенда. Статус: {stand_resp.status}")
                            else:
                                stand_text = await stand_resp.text()
                                logger.warning(f"[{user_id}] Эндпоинт стенда вернул ошибку {stand_resp.status}: {stand_text}")
                    except Exception as e:
                        logger.error(f"[{user_id}] Ошибка при отправке данных на эндпоинт стенда: {e}", exc_info=True)
                else:
                    logger.info(f"[{user_id}] В ответе основного API не найдено 'external_id'. Дополнительный запрос не выполняется.")
            if not resp.ok:
                error_msg = data.get('error', f'Ошибка {resp.status}')
                logger.error(f"API инфраструктуры вернул ошибку: {error_msg}")
                return [{"type": "text", "content": f"Не удалось найти информацию: {error_msg}"}]

            if data.get("static_map") and data.get("interactive_map"):
                caption = data.get("answer", f"Результаты по вашему запросу на карте.")
                base_url = "https://testecobot.ru/maps/"
                static_filename = data["static_map"].replace(base_url, "")
                interactive_filename = data["interactive_map"].replace(base_url, "")
                s_encoded = base_url + quote(static_filename)
                i_encoded = base_url + quote(interactive_filename)
                return [{"type": "map", "static": s_encoded, "interactive": i_encoded, "caption": caption}]
            else:
                text_response = data.get("answer", "По вашему запросу ничего не найдено.")
                if data.get("objects"):
                    objects_list = [obj["name"] for obj in data.get("objects", []) if "name" in obj]
                    if objects_list:
                        text_response += f"\n\nНайдены объекты:\n• " + "\n• ".join(objects_list)
                return [{"type": "text", "content": text_response}]
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при запросе к API инфраструктуры")
        return [{"type": "text", "content": "Сервер инфраструктуры не отвечает. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Критическая ошибка в handle_draw_map_of_infrastructure: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске объектов на карте."}]

async def handle_objects_in_polygon(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    if not geo_nom:
        logger.error("Ошибка в handle_objects_in_polygon: не найден `secondary_entity` в анализе.")
        return [{"type": "text", "content": "Не указано место для поиска объектов."}]
    
    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {"name": geo_nom, "buffer_radius_km": 5}
    logger.debug(f"Запрос к `objects_in_polygon` с payload: {payload}")
    
    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                logger.error(f"API `objects_in_polygon` вернул ошибку {resp.status} для '{geo_nom}'")
                return [{"type": "text", "content": f"Не удалось найти информацию для '{geo_nom}'."}]
            
            data = await resp.json()
            objects_list = data.get("all_biological_names", [])

            if objects_list:
                caption = f"🗺️ **В районе «{geo_nom}» я нашел {len(objects_list)} биологических объектов.**\n\nХотите увидеть краткий умный обзор или посмотреть полный список?"
            else:
                caption = f"В районе «{geo_nom}» не найдено известных мне биологических объектов."

            buttons = []
            if len(objects_list) > 0:
                buttons.append([
                    {"text": "🎯 Умный обзор", "callback_data": f"explore:overview:{geo_nom}"},
                    {"text": "📋 Полный список", "callback_data": f"explore:full_list:{geo_nom}"}
                ])
            
            interactive_map_url = data.get("interactive_map")
            if interactive_map_url:
                buttons.append([
                    {"text": "🌍 Посмотреть на интерактивной карте", "url": interactive_map_url}
                ])
            if data.get("static_map"):
                logger.debug(f"Найдена карта для '{geo_nom}'. Отправка карты с проактивными кнопками.")
                return [{
                    "type": "clarification_map",
                    "static_map": data["static_map"],
                    "content": caption,
                    "buttons": buttons
                }]
            else:
                logger.debug(f"Карта не найдена для '{geo_nom}'. Отправка текста с проактивными кнопками.")
                return [{
                    "type": "clarification",
                    "content": caption,
                    "buttons": buttons
                }]

    except Exception as e:
        logger.error(f"Критическая ошибка в `handle_objects_in_polygon`: {e}", exc_info=True)
        return [{"type": "text", "content": f"Произошла внутренняя ошибка при поиске объектов в «{geo_nom}»."}]

async def handle_geo_request(session: aiohttp.ClientSession, analysis: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    primary_entity = analysis.get("primary_entity") or {}
    secondary_entity = analysis.get("secondary_entity") or {}

    location_name = secondary_entity.get("name", "")
    if not location_name and primary_entity.get("type") == "GeoPlace":
        location_name = primary_entity.get("name", "")
    
    raw_entity_name = primary_entity.get("name")
    entity_category = primary_entity.get("category", "Достопримечательности")
    
    canonical_entity_name = normalize_entity_name(raw_entity_name)
    
    specific_types_list = []
    if canonical_entity_name:
        if canonical_entity_name in GROUP_ENTITY_MAP:
            specific_types_list = GROUP_ENTITY_MAP[canonical_entity_name]
        else:
            specific_types_list = [canonical_entity_name]
    
    baikal_relation = determine_baikal_relation(
        query=original_query,
        entity_name=primary_entity.get("name", ""),
        entity_type=primary_entity.get("type", "")
    )
    
    location_info = {"nearby_places": []}
    
    if baikal_relation:
        import re
        baikal_pattern = re.compile(r'байкал?[а-я]*')
        if location_name and not baikal_pattern.search(location_name.lower()):
            location_info["exact_location"] = location_name
            location_info["region"] = ""
        else:
            location_info["exact_location"] = ""
            location_info["region"] = ""
    else:
        location_info["exact_location"] = location_name
        location_info["region"] = ""
    
    geo_type_payload = {
        "primary_type": [entity_category],
        "specific_types": specific_types_list
    }
        
    payload = {
        "location_info": location_info,
        "geo_type": geo_type_payload
    }
    
    if baikal_relation:
        payload["baikal_relation"] = baikal_relation
    
    if should_include_object_name(raw_entity_name):
        url = f"{API_URLS['find_geo_special_description']}?query={original_query}&use_gigachat_answer=true&debug_mode={str(debug_mode).lower()}&object_name={raw_entity_name}"
    else:
        url = f"{API_URLS['find_geo_special_description']}?query={original_query}&use_gigachat_answer=true&debug_mode={str(debug_mode).lower()}"
    
    logger.info(f"Запрос к {url} с payload: {payload}")
    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                logger.warning(f"Запрос к /object/description прошел с ошибкой - {resp.status}")
                return [{"type": "text", "content": "Извините, информация по этому запросу временно недоступна."}]
            
            data = await resp.json()
            user_settings = get_user_settings(user_id)
            is_stand_session_active = False
            if user_settings.get("on_stand"):
                last_active_time = user_settings.get("stand_last_active", 0)
                time_elapsed = time.time() - last_active_time
                
                if time_elapsed < STAND_SESSION_TIMEOUT:
                    is_stand_session_active = True
                    update_user_settings(user_id, {"stand_last_active": time.time()})
                    logger.info(f"[{user_id}] Сессия 'у стенда' активна и продлена.")
                else:
                    update_user_settings(user_id, {"on_stand": False, "stand_last_active": None})
                    logger.info(f"[{user_id}] Сессия 'у стенда' истекла по таймауту. Флаг сброшен.")
            if is_stand_session_active:
                logger.info(f"[{user_id}] Пользователь со стенда. Запускаем доп. логику для handle_geo_request.")
                
                external_ids = []
                if "external_ids" in data and isinstance(data.get("external_ids"), list):
                    external_ids = data["external_ids"]
                
                if external_ids:
                    logger.info(f"[{user_id}] Найдено {len(external_ids)} external_id для отправки: {external_ids}")
                    stand_payload = {
                        "items": [{"id": ext_id} for ext_id in external_ids],
                        "secret_key": STAND_SECRET_KEY
                    }
                    try:
                        stand_url = API_URLS['stand_endpoint']
                        async with session.post(stand_url, json=stand_payload, timeout=10) as stand_resp:
                            if stand_resp.ok:
                                logger.info(f"[{user_id}] Данные успешно отправлены на эндпоинт стенда. Статус: {stand_resp.status}")
                            else:
                                stand_text = await stand_resp.text()
                                logger.warning(f"[{user_id}] Эндпоинт стенда вернул ошибку {stand_resp.status}: {stand_text}")
                    except Exception as e:
                        logger.error(f"[{user_id}] Ошибка при отправке данных на эндпоинт стенда: {e}", exc_info=True)
                else:
                    logger.info(f"[{user_id}] В ответе API find_geo_special_description не найдено 'external_id' в meta_info. Дополнительный запрос не выполняется.")

            final_responses = []

            gigachat_answer = data.get("gigachat_answer")
            if gigachat_answer and gigachat_answer.strip():
                logger.info("Используем ответ от GigaChat.")
                final_responses.append({"type": "text", "content": gigachat_answer.strip()})

            elif descriptions := data.get("descriptions"):
                logger.info("Ответ GigaChat отсутствует. Ищем в 'descriptions'.")
                first_valid_index = -1
                for i, desc in enumerate(descriptions):
                    if content := desc.get("content"):
                        if content.strip():
                            final_responses.append({"type": "text", "content": content.strip()})
                            first_valid_index = i
                            break
                
                if first_valid_index != -1:
                    remaining_titles = []
                    for desc in descriptions[first_valid_index + 1:]:
                        if title := desc.get("title"):
                            if title.strip():
                                remaining_titles.append(title.strip())
                        if len(remaining_titles) >= 10:
                            break
                    
                    if remaining_titles:
                        title_list_str = "\n".join(f"• {title}" for title in remaining_titles)
                        full_title_message = f"Также могут быть интересны:\n{title_list_str}"
                        final_responses.append({"type": "text", "content": full_title_message})

            if not final_responses:
                 final_responses.append({"type": "text", "content": "К сожалению, по вашему запросу ничего не найдено."})

            return final_responses

    except Exception as e:
        logger.error(f"Критическая ошибка в `handle_geo_request`: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске информации."}]
 