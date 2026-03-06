import os
import aiohttp
import logging
import asyncio
from typing import Dict, Any, Optional, List, Callable, Awaitable
from urllib.parse import quote

from config import API_URLS, DEFAULT_TIMEOUT, STAND_SECRET_KEY, TIMEOUT_FOR_OBJECTS_IN_POLYGON
from utils.settings_manager import get_user_settings
from utils.error_logger import send_error_log, log_api_error
from logic.entity_normalizer import normalize_entity_name_for_maps, ENTITY_MAP, should_include_object_name
from logic.baikal_context import determine_baikal_relation
from logic.stand_manager import is_stand_session_active

# Импортируем нашу модель
from core.model import CoreResponse

logger = logging.getLogger(__name__)

# --- Вспомогательная функция (Внутренняя) ---

async def _get_map_from_api(
    session: aiohttp.ClientSession, 
    url: str, 
    payload: dict, 
    analysis: dict, 
    debug_mode: bool, 
    stoplist_param: int, 
    user_id: str, 
    geo_name: str = None
) -> List[CoreResponse]:
    """
    Делает запрос к API карт и возвращает список CoreResponse.
    """
    full_url = f"{url}?debug_mode={str(debug_mode).lower()}&in_stoplist={stoplist_param}"
    responses = []

    if debug_mode:
        responses.append(CoreResponse(
            type="debug", 
            content=f"🐞 **API Request (Map)**\nURL: `{full_url}`\nPayload: `{payload}`"
        ))

    async with session.post(full_url, json=payload, timeout=DEFAULT_TIMEOUT) as map_resp:
        if not map_resp.ok:
            await log_api_error(session, user_id, full_url, map_resp.status, await map_resp.text(), str(payload), context=analysis)
            return [CoreResponse(type="text", content="Не удалось построить карту.")]

        api_data = await map_resp.json()
        
        # Логика формирования подписи
        object_name = analysis.get("primary_entity", {}).get("name", "Объект")
        names = sorted(list(set(name.capitalize() for name in api_data.get("names", []))))
        
        caption = ""
        if names:
            text_header = f"📍 Рядом с '{geo_name}' вы можете встретить '{object_name}' в местах:\n" if geo_name else f"📍 '{object_name.capitalize()}' встречается в местах:\n"
            caption = text_header + "• " + "\n• ".join(names)

        if api_data.get("status") == "no_objects":
            text = f"К сожалению, я не нашел '{object_name}'" + (f" поблизости от '{geo_name}'." if geo_name else " на карте.")
            responses.append(CoreResponse(type="text", content=text))

        # Если есть карта
        if api_data.get("interactive_map") and api_data.get("static_map"):
            responses.append(CoreResponse(
                type="map",
                static_map=api_data["static_map"],
                interactive_map=api_data["interactive_map"],
                content=caption, # Подпись к карте
                used_objects=api_data.get("used_objects", [])
            ))
        elif caption:
            # Если карты нет, но есть список мест
            responses.append(CoreResponse(type="text", content=caption))
        
        return responses

# --- Основные обработчики ---

async def handle_nearest(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str,
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    object_nom = analysis.get("primary_entity", {}).get("name")
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    
    if not object_nom or not geo_nom:
        return [CoreResponse(type="text", content="Недостаточно данных для поиска: нужен и объект, и место.")]

    if on_status:
        await on_status(f"🗺️ Ищу {object_nom} рядом с {geo_nom}...")

    try:
        # 1. Получаем координаты места
        async with session.post(API_URLS["get_coords"], json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok: 
                return [CoreResponse(type="text", content=f"Не удалось найти координаты для '{geo_nom}'.")]
            coords = await resp.json()

        user_settings = get_user_settings(user_id)
        stoplist_param = 1 if user_settings.get("stoplist_enabled", True) else 2

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
            stoplist_param=stoplist_param,
            user_id=user_id,
            geo_name=geo_nom
        )
    except Exception as e:
        logger.error(f"Ошибка в handle_nearest: {e}", exc_info=True)
        await send_error_log(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Произошла внутренняя ошибка при поиске ближайших мест.")]


async def handle_draw_locate_map(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str,
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    object_nom = analysis.get("primary_entity", {}).get("name")
    if not object_nom: 
        return [CoreResponse(type="text", content="Не указан объект для отображения на карте.")]
    
    if on_status:
        await on_status(f"🗺️ Строю карту ареала для {object_nom}...")

    user_settings = get_user_settings(user_id)
    stoplist_param = 1 if user_settings.get("stoplist_enabled", True) else 2

    # Координаты центра Байкала (примерно) и большой радиус
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
        debug_mode=debug_mode,
        stoplist_param=stoplist_param,
        user_id=user_id
    )


async def handle_draw_map_of_infrastructure(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str,
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    primary_entity = analysis.get("primary_entity") or {}
    secondary_entity = analysis.get("secondary_entity") or {}
    raw_object_name = primary_entity.get("name")
    area_name = secondary_entity.get("name", "")

    if not raw_object_name:
        return [CoreResponse(type="text", content="Не смог определить, что нужно найти на карте.")]
    
    if on_status:
        await on_status(f"🗺️ Ищу инфраструктуру: {raw_object_name}...")

    normalized_type = normalize_entity_name_for_maps(raw_object_name)
    is_specific_name_search = normalized_type not in ENTITY_MAP.values()

    payload = {"limit": 10}
    if is_specific_name_search:
        payload["object_name"] = raw_object_name
        if area_name: payload["area_name"] = area_name
    else:
        payload["object_type"] = normalized_type
        if not area_name:
             return [CoreResponse(type="text", content=f"Пожалуйста, уточните, где вы хотите найти '{raw_object_name}'?")]
        payload["area_name"] = area_name

    try:
        url = f"{API_URLS['show_map_infrastructure']}?debug_mode={str(debug_mode).lower()}"
        responses = []
        if debug_mode:
            responses.append(CoreResponse(type="debug", content=f"🐞 **API Request**\nURL: `{url}`\nPayload: `{payload}`"))

        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            # Проверка Content-Type (была в старом коде)
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'application/json' not in content_type:
                await log_api_error(session, user_id, url, resp.status, f"Invalid Content-Type: {content_type}", original_query, context=analysis)
                return [CoreResponse(type="text", content="Ошибка сервера инфраструктуры.")]

            api_data = await resp.json()

            if not resp.ok:
                error_msg = api_data.get('error', f'Ошибка {resp.status}')
                return [CoreResponse(type="text", content=f"Не удалось найти информацию: {error_msg}")]

            # --- Логика Стенда (Бизнес-логика) ---
            if is_stand_session_active(user_id):
                # Отправляем данные на стенд в фоне, не блокируя ответ пользователю
                # В реальном продакшене лучше использовать background tasks, но здесь оставим await для простоты миграции
                external_ids = [obj["external_id"] for obj in api_data.get("objects", []) if isinstance(obj, dict) and obj.get("external_id")]
                if external_ids:
                    stand_payload = {"items": [{"id": eid} for eid in external_ids], "secret_key": STAND_SECRET_KEY}
                    try:
                        async with session.post(API_URLS['stand_endpoint'], json=stand_payload, timeout=5) as stand_resp:
                            if not stand_resp.ok: logger.warning(f"Stand error: {stand_resp.status}")
                    except Exception as ex:
                        logger.error(f"Stand connection error: {ex}")

            # Формирование ответа
            if api_data.get("static_map") and api_data.get("interactive_map"):
                caption = api_data.get("answer", f"Результаты по вашему запросу на карте.")
                
                # Кодирование URL (сохраняем логику кодирования кириллицы в URL)
                base_url = os.getenv("BASE_URL_MAPS")
                static_filename = api_data["static_map"].replace(base_url, "")
                interactive_filename = api_data["interactive_map"].replace(base_url, "")
                s_encoded = base_url + quote(static_filename)
                i_encoded = base_url + quote(interactive_filename)
                
                responses.append(CoreResponse(
                    type="map",
                    static_map=s_encoded,
                    interactive_map=i_encoded,
                    content=caption,
                    used_objects=api_data.get("used_objects", [])
                ))
            else:
                text_response = api_data.get("answer", "По вашему запросу ничего не найдено.")
                if api_data.get("objects"):
                    obj_names = [obj["name"] for obj in api_data["objects"] if "name" in obj]
                    if obj_names:
                        text_response += f"\n\nНайдены объекты:\n• " + "\n• ".join(obj_names)
                responses.append(CoreResponse(type="text", content=text_response, used_objects=api_data.get("used_objects", [])))

            return responses

    except Exception as e:
        logger.error(f"Ошибка в infrastructure: {e}", exc_info=True)
        await send_error_log(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Произошла внутренняя ошибка при поиске.")]


async def handle_objects_in_polygon(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, # Добавил user_id в сигнатуру для логирования
    original_query: str,
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    subtype = analysis.get("primary_entity", {}).get("category")
    subtype_mapping = {"Fauna": "Объект фауны", "Flora": "Объект флоры"}   
    object_subtype = subtype_mapping.get(subtype)
    
    if not geo_nom:
        return [CoreResponse(type="text", content="Не указано место для поиска объектов.")]
    
    if on_status:
        await on_status(f"🌿 Сканирую местность: {geo_nom}...")

    url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
    payload = {
        "name": geo_nom, 
        "buffer_radius_km": 1, 
        "object_type": "biological_entity", 
        "object_subtype": object_subtype
    }
    
    responses = []
    if debug_mode:
        responses.append(CoreResponse(type="debug", content=f"🐞 **API Request**\nURL: `{url}`\nPayload: `{payload}`"))

    try:
        async with session.post(url, json=payload, timeout=TIMEOUT_FOR_OBJECTS_IN_POLYGON) as resp:
            if not resp.ok:
                await log_api_error(session, user_id or "unknown", url, resp.status, await resp.text(), original_query, context=analysis)
                return [CoreResponse(type="text", content=f"Не удалось найти информацию для '{geo_nom}'.")]

            api_data = await resp.json()
            objects_list = api_data.get("all_biological_names", [])

            caption = f"В районе «{geo_nom}» не найдено объектов."
            if objects_list:
                caption = f"🗺️ **В районе «{geo_nom}» я нашел {len(objects_list)} биологических объектов.**\n\nХотите умный обзор или полный список?"

            # Формируем кнопки
            buttons = []
            if len(objects_list) > 0:
                buttons.append([
                    {"text": "🎯 Умный обзор", "callback_data": f"explore:overview:{geo_nom}"},
                    {"text": "📋 Полный список", "callback_data": f"explore:full_list:{geo_nom}"}
                ])
            
            if api_data.get("interactive_map"):
                buttons.append([{"text": "🌍 Интерактивная карта", "url": api_data["interactive_map"]}])
            
            # Определяем тип ответа (карта с кнопками или текст с кнопками)
            if api_data.get("static_map"):
                responses.append(CoreResponse(
                    type="clarification_map",
                    static_map=api_data["static_map"],
                    content=caption,
                    buttons=buttons,
                    used_objects=api_data.get("used_objects", [])
                ))
            else:
                responses.append(CoreResponse(
                    type="clarification",
                    content=caption,
                    buttons=buttons,
                    used_objects=api_data.get("used_objects", [])
                ))
                
            return responses

    except Exception as e:
        logger.error(f"Ошибка objects_in_polygon: {e}", exc_info=True)
        await send_error_log(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content=f"Ошибка при поиске объектов в «{geo_nom}».")]


async def handle_geo_request(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    original_query: str, 
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    
    clean_query = analysis.get("search_query", original_query)
    
    if on_status:
        await on_status("🗺️ Ищу информацию о месте...")

    primary_entity = analysis.get("primary_entity") or {}
    secondary_entity = analysis.get("secondary_entity") or {}
    
    # 1. Логика определения имени локации
    location_name = secondary_entity.get("name", "")
    if not location_name and primary_entity.get("type") == "GeoPlace":
        location_name = primary_entity.get("name", "")
    
    raw_entity_name = primary_entity.get("name")
    entity_category = primary_entity.get("category", "Достопримечательности")
    entity_subcategory = primary_entity.get("subcategory")

    queries_to_try = [original_query]
    if clean_query != original_query: queries_to_try.append(clean_query)

    api_data = None

    responses = []
    if debug_mode:
        responses.append(CoreResponse(type="debug", content=f"Original: {original_query}\nClean: {clean_query}"))

    try:
        # 2. Цикл попыток запроса (с оригинальным и очищенным)
        for i, query_text in enumerate(queries_to_try):
            baikal_relation = determine_baikal_relation(query_text, primary_entity.get("name", ""), primary_entity.get("type", ""))
            
            location_info = {"nearby_places": [], "exact_location": "", "region": ""}
            if not baikal_relation and location_name != "Байкал":
                location_info["exact_location"] = location_name

            geo_type_payload = {
                "primary_type": [entity_category] if entity_category else [],
                "specific_types": entity_subcategory if entity_subcategory else []
            }
                
            payload = {"location_info": location_info, "geo_type": geo_type_payload}
            if baikal_relation: payload["baikal_relation"] = baikal_relation
            
            base_url = API_URLS['find_geo_special_description']
            params = f"query={query_text}&force_vector_search=true&use_gigachat_answer=true&debug_mode={str(debug_mode).lower()}"
            if should_include_object_name(raw_entity_name):
                params += f"&object_name={raw_entity_name}"
            
            url = f"{base_url}?{params}"

            async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.ok:
                    api_data = await resp.json()
                    break # Успех, выходим из цикла
                elif i == len(queries_to_try) - 1:
                    # Если это была последняя попытка и она провалилась
                    await log_api_error(session, user_id, url, resp.status, await resp.text(), query_text, context=analysis)
                    return [CoreResponse(type="text", content="Извините, информация временно недоступна.")]

        # 3. Логика Стенда (СОХРАНЕНА ПОЛНОСТЬЮ)
        if is_stand_session_active(user_id) and api_data:
            external_ids = api_data.get("external_id", [])
            # Иногда API возвращает external_id как список внутри dict, иногда как список. Проверка на всякий случай.
            if not isinstance(external_ids, list) and "external_id" in api_data: 
                 external_ids = api_data["external_id"]
                 
            if external_ids and isinstance(external_ids, list):
                stand_payload = {"items": [{"id": eid} for eid in external_ids], "secret_key": STAND_SECRET_KEY}
                try:
                    async with session.post(API_URLS['stand_endpoint'], json=stand_payload, timeout=5) as stand_resp:
                        if not stand_resp.ok: logger.warning(f"Stand error geo: {stand_resp.status}")
                except Exception: pass

        # 4. Формирование ответа (ВОССТАНОВЛЕНА ЛОГИКА "Остальных заголовков")
        gigachat_answer = api_data.get("gigachat_answer")
        
        if gigachat_answer and gigachat_answer.strip():
            # Если есть ответ от GigaChat - берем его
            responses.append(CoreResponse(
                type="text", 
                content=gigachat_answer.strip(),
                used_objects=api_data.get("used_objects", [])
            ))
        
        elif descriptions := api_data.get("descriptions"):
            # Если нет GigaChat, ищем описания
            first_valid_index = -1
            text_content = ""

            # Ищем первое непустое описание
            for i, desc in enumerate(descriptions):
                if content := desc.get("content"):
                    if content.strip():
                        text_content = content.strip()
                        first_valid_index = i
                        break
            
            if text_content:
                responses.append(CoreResponse(
                    type="text", 
                    content=text_content,
                    used_objects=api_data.get("used_objects", [])
                ))

            # Логика сбора "Также могут быть интересны" (остальные заголовки)
            if first_valid_index != -1:
                remaining_titles = []
                for desc in descriptions[first_valid_index + 1:]:
                    if title := desc.get("title"):
                        if cleaned_title := title.strip():
                            remaining_titles.append(cleaned_title)
                    if len(remaining_titles) >= 5:
                        break

                if remaining_titles:
                    title_list_str = "\n".join(f"• {title}" for title in remaining_titles)
                    full_title_message = f"Также могут быть интересны:\n{title_list_str}"
                    responses.append(CoreResponse(type="text", content=full_title_message))
            # ============================================

        if not responses or (len(responses) == 1 and responses[0].type == 'debug'):
             responses.append(CoreResponse(type="text", content="К сожалению, по вашему запросу ничего не найдено."))

        return responses

    except Exception as e:
        logger.error(f"Ошибка geo_request: {e}", exc_info=True)
        await send_error_log(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Внутренняя ошибка поиска.")]

async def handle_draw_map_of_list_stub(
    session: aiohttp.ClientSession, 
    analysis: dict, 
    user_id: str, 
    debug_mode: bool,
    **kwargs # Заглушка для совместимости аргументов
) -> List[CoreResponse]:
    """
    Заглушка для показа карты из контекста.
    """
    context_objects = analysis.get("used_objects_from_context")
    if not context_objects:
        return [CoreResponse(type="text", content="[DEBUG] Ошибка: Контекст не передан.")]
        
    names = [obj.get("name") for obj in context_objects if obj.get("name")]
    
    msg = f"✅ **Контекст передан!**\nОбъектов: {len(names)}\n\n• " + "\n• ".join(names)
    return [CoreResponse(type="text", content=msg)]