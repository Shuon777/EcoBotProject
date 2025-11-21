import aiohttp
import logging
import time
import asyncio
from typing import Dict, Any
from urllib.parse import quote
from config import API_URLS, DEFAULT_TIMEOUT, STAND_SECRET_KEY, STAND_SESSION_TIMEOUT
from utils.settings_manager import get_user_settings, update_user_settings
from utils.bot_utils import create_structured_response
from logic.entity_normalizer_for_maps import normalize_entity_name_for_maps, ENTITY_MAP
from logic.entity_normalizer import normalize_entity_name, GROUP_ENTITY_MAP, should_include_object_name
from logic.baikal_context import determine_baikal_relation
from logic.stand_manager import is_stand_session_active

logger = logging.getLogger(__name__)

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, analysis: dict, debug_mode: bool, geo_name: str = None) -> list:
    async with session.post(f"{url}?debug_mode={str(debug_mode).lower()}", json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        if not map_resp.ok: 
            return [{"type": "text", "content": "Не удалось построить карту."}]

        api_data = await map_resp.json()
        user_messages = []
        object_name = analysis.get("primary_entity", {}).get("name") # Извлекаем имя из analysis

        names = sorted(list(set(name.capitalize() for name in api_data.get("names", []))))
        caption = ""
        if names:
            text_header = f"📍 Рядом с '{geo_name}' вы можете встретить '{object_name}' в местах:\n" if geo_name else f"📍 '{object_name.capitalize()}' встречается в местах:\n"
            caption = text_header + "• " + "\n• ".join(names)

        if api_data.get("status") == "no_objects":
            text = f"К сожалению, я не нашел '{object_name}'" + (f" поблизости от '{geo_name}'." if geo_name else " на карте.")
            user_messages.append({"type": "text", "content": text})

        if api_data.get("interactive_map") and api_data.get("static_map"):
            map_message = {
                "type": "map", 
                "static": api_data["static_map"], 
                "interactive": api_data["interactive_map"], 
                "caption": caption
            }
            user_messages.append(map_message)
        elif caption:
            user_messages.append({"type": "text", "content": caption})
        return create_structured_response(api_data, user_messages)

async def handle_nearest(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    object_nom = analysis.get("primary_entity", {}).get("name")
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    if not object_nom or not geo_nom:
        return [{"type": "text", "content": "Недостаточно данных для поиска: нужен и объект, и место."}]

    try:
        logger.info(f"Обращение к get_coords с payload - name: {geo_nom}")
        async with session.post(API_URLS["get_coords"], json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok: 
                return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
            coords = await resp.json()
        logger.info(f"Ответ от get_coords - {coords}")

        payload = {
            "latitude": coords.get("latitude"), 
            "longitude": coords.get("longitude"), 
            "radius_km": 50, 
            "species_name": object_nom, 
            "object_type": "geographical_entity"
        }
        
        return await _get_map_from_api(
            session=session,
            url=API_URLS["coords_to_map"],
            payload=payload,
            analysis=analysis, 
            debug_mode=debug_mode,
            geo_name=geo_nom
        )

    except Exception as e:
        logger.error(f"Ошибка в handle_nearest: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске ближайших мест."}]

async def handle_draw_locate_map(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    object_nom = analysis.get("primary_entity", {}).get("name")
    if not object_nom: 
        return [{"type": "text", "content": "Не указан объект для отображения на карте."}]
    
    payload = {
        "latitude": 53.27612, 
        "longitude": 107.3274, 
        "radius_km": 500000, 
        "species_name": object_nom, 
        "object_type": "geographical_entity"
    }
    
    return await _get_map_from_api(
        session=session,
        url=API_URLS["coords_to_map"],
        payload=payload,
        analysis=analysis, 
        debug_mode=debug_mode
    )

async def handle_draw_map_of_infrastructure(session: aiohttp.ClientSession, analysis: dict, user_id: str, debug_mode: bool) -> list:
    """
    Обрабатывает запросы на отображение инфраструктуры на карте.
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

            api_data = await resp.json()

            if is_stand_session_active(user_id):
                logger.info(f"[{user_id}] Пользователь со стенда. Запускаем дополнительную логику.")
                
                external_ids = []
                if "objects" in api_data and isinstance(api_data["objects"], list):
                    for obj in api_data["objects"]:
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
                error_msg = api_data.get('error', f'Ошибка {resp.status}')
                logger.error(f"API инфраструктуры вернул ошибку: {error_msg}")
                return [{"type": "text", "content": f"Не удалось найти информацию: {error_msg}"}]
            
            user_messages = []
            
            if api_data.get("static_map") and api_data.get("interactive_map"):
                caption = api_data.get("answer", f"Результаты по вашему запросу на карте.")
                base_url = "https://testecobot.ru/maps/"
                static_filename = api_data["static_map"].replace(base_url, "")
                interactive_filename = api_data["interactive_map"].replace(base_url, "")
                s_encoded = base_url + quote(static_filename)
                i_encoded = base_url + quote(interactive_filename)
                user_messages.append({"type": "map", "static": s_encoded, "interactive": i_encoded, "caption": caption})
            else:
                text_response = api_data.get("answer", "По вашему запросу ничего не найдено.")
                if api_data.get("objects"):
                    objects_list = [obj["name"] for obj in api_data.get("objects", []) if "name" in obj]
                    if objects_list:
                        text_response += f"\n\nНайдены объекты:\n• " + "\n• ".join(objects_list)
                user_messages.append({"type": "text", "content": text_response})

            return create_structured_response(api_data, user_messages)

    except asyncio.TimeoutError:
        logger.error(f"Таймаут при запросе к API инфраструктуры")
        return [{"type": "text", "content": "Сервер инфраструктуры не отвечает. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Критическая ошибка в handle_draw_map_of_infrastructure: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске объектов на карте."}]

async def handle_objects_in_polygon(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    subtype = analysis.get("primary_entity", {}).get("category")
    subtype_mapping = {
    "Fauna": "Объект фауны",
    "Flora": "Объект флоры"
    }   
    object_subtype = subtype_mapping.get(subtype)
    if not geo_nom:
        logger.error("Ошибка в handle_objects_in_polygon: не найден `secondary_entity` в анализе.")
        return [{"type": "text", "content": "Не указано место для поиска объектов."}]
    
    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {"name": geo_nom, 
            "buffer_radius_km": 5, 
            "object_type": "biological_entity", 
            "object_subtype": object_subtype}
    logger.info(f"Запрос к `objects_in_polygon` с payload: {payload}")
    
    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                logger.error(f"API `objects_in_polygon` вернул ошибку {resp.status} для '{geo_nom}'")
                return [{"type": "text", "content": f"Не удалось найти информацию для '{geo_nom}'."}]

            api_data = await resp.json()
            user_messages = []
            
            objects_list = api_data.get("all_biological_names", [])

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
            
            interactive_map_url = api_data.get("interactive_map")
            if interactive_map_url:
                buttons.append([
                    {"text": "🌍 Посмотреть на интерактивной карте", "url": interactive_map_url}
                ])
                
            if api_data.get("static_map"):
                logger.debug(f"Найдена карта для '{geo_nom}'. Отправка карты с проактивными кнопками.")
                clarification_message = {
                    "type": "clarification_map",
                    "static_map": api_data["static_map"],
                    "content": caption,
                    "buttons": buttons
                }
                user_messages.append(clarification_message)
            else:
                logger.debug(f"Карта не найдена для '{geo_nom}'. Отправка текста с проактивными кнопками.")
                clarification_message = {
                    "type": "clarification",
                    "content": caption,
                    "buttons": buttons
                }
                user_messages.append(clarification_message)

            return create_structured_response(api_data, user_messages)

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
    entity_subcategory = primary_entity.get("subcategory")
    
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
    elif location_name == "Байкал":
        location_info["exact_location"] = ""
        location_info["region"] = ""
    else:
        location_info["exact_location"] = location_name
        location_info["region"] = ""
    
    geo_type_payload = {
        "primary_type": [entity_category],
        "specific_types": entity_subcategory
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
            
            # [ИЗМЕНЕНИЕ] Шаг 2: Получаем и сохраняем полный ответ от API
            api_data = await resp.json()

            # --- Логика для стенда остается без изменений, она работает с `api_data` ---
            if is_stand_session_active(user_id):
                # ... (весь ваш существующий код для стенда)
                logger.info(f"[{user_id}] Пользователь со стенда. Запускаем доп. логику для handle_geo_request.")
                
                external_ids = []
                if "external_ids" in api_data and isinstance(api_data.get("external_ids"), list):
                    external_ids = api_data["external_ids"]
                
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


            # [ИЗМЕНЕНИЕ] Шаг 3: Обрабатываем ответ и готовим сообщения для пользователя
            user_messages = []

            gigachat_answer = api_data.get("gigachat_answer")
            if gigachat_answer and gigachat_answer.strip():
                logger.info("Используем ответ от GigaChat.")
                user_messages.append({"type": "text", "content": gigachat_answer.strip()})

            elif descriptions := api_data.get("descriptions"):
                logger.info("Ответ GigaChat отсутствует. Ищем в 'descriptions'.")
                first_valid_index = -1
                for i, desc in enumerate(descriptions):
                    if content := desc.get("content"):
                        if content.strip():
                            user_messages.append({"type": "text", "content": content.strip()})
                            first_valid_index = i
                            break
                
                if first_valid_index != -1:
                    remaining_titles = []
                    for desc in descriptions[first_valid_index + 1:]:
                        if title := desc.get("title"):
                            if title.strip():
                                cleaned_title = title.strip().split('.')[0].strip()
                                if cleaned_title:
                                    remaining_titles.append(cleaned_title + ".")
                        if len(remaining_titles) >= 5:
                            break
                    
                    if remaining_titles:
                        title_list_str = "\n".join(f"• {title}" for title in remaining_titles)
                        full_title_message = f"Также могут быть интересны:\n{title_list_str}"
                        user_messages.append({"type": "text", "content": full_title_message})

            if not user_messages:
                 user_messages.append({"type": "text", "content": "К сожалению, по вашему запросу ничего не найдено."})

            # [ИЗМЕНЕНИЕ] Шаг 4: Вызываем помощника для упаковки метаданных
            return create_structured_response(api_data, user_messages)

    except Exception as e:
        logger.error(f"Критическая ошибка в `handle_geo_request`: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске информации."}]

async def handle_draw_map_of_list_stub(session: aiohttp.ClientSession, analysis: dict, user_id: str, debug_mode: bool) -> list:
    """
    [ЗАГЛУШКА] Обработчик для отрисовки списка объектов на карте.
    Проверяет, что `used_objects_from_context` были успешно переданы.
    Вместо карты возвращает отладочное сообщение.
    """
    logger.info(f"[{user_id}] ВЫЗВАН ОБРАБОТЧИК-ЗАГЛУШКА: handle_draw_map_of_list_stub")
    
    context_objects = analysis.get("used_objects_from_context")
    
    if not context_objects:
        logger.warning(f"[{user_id}] Заглушка вызвана, но 'used_objects_from_context' не найдены в analysis.")
        return [{"type": "text", "content": "[DEBUG] Ошибка: Контекст со списком объектов не был передан."}]
        
    object_names = [obj.get("name") for obj in context_objects if obj.get("name")]
    
    if not object_names:
        return [{"type": "text", "content": "[DEBUG] Ошибка: Объекты в контексте есть, но без имен."}]

    # Формируем отладочное сообщение для пользователя
    debug_message = (
        f"✅ **Контекст успешно передан!**\n\n"
        f"**Действие:** `Показать на карте`\n"
        f"**Количество объектов:** `{len(object_names)}`\n\n"
        f"**Список объектов для отрисовки:**\n"
        f"• " + "\n• ".join(object_names)
    )
    
    # Возвращаем это сообщение. Метаданные здесь не нужны, т.к. это конец цепочки.
    return [{"type": "text", "content": debug_message, "parse_mode": "Markdown"}]