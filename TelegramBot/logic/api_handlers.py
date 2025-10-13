# --- НАЧАЛО ФАЙЛА: logic/api_handlers.py ---

import aiohttp
import asyncio
import logging
import base64
import json
from typing import Dict, Any, List

from config import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from utils.settings_manager import get_user_settings
from utils.context_manager import RedisContextManager
from logic.entity_normalizer import normalize_entity_name

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
async def check_simplified_search(session: aiohttp.ClientSession, object_nom: str, features: dict, debug_mode: bool) -> bool:
    """Проверяет, вернет ли упрощенный запрос с заданными признаками результаты."""
    try:
        url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
        payload = {"species_name": object_nom, "features": features}
        
        logger.debug(f"Проверка упрощенного запроса: {object_nom} с features: {features}")
        
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                has_images = bool(data.get("images"))
                logger.debug(f"Результат проверки для {object_nom} {features}: {has_images}")
                return has_images
            return False
    except Exception as e:
        logger.warning(f"Ошибка при проверке упрощенного запроса для {object_nom}: {e}")
        return False

async def handle_get_picture(session: aiohttp.ClientSession, analysis: dict, user_id: str, debug_mode: bool) -> list:
    logger.info(f"--- Запуск handle_get_picture с analysis: {analysis} ---")
    
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    attributes = analysis.get("attributes", {})
    
    if not object_nom:
        return [{"type": "text", "content": "Не указан объект для поиска изображения."}]

    features = {}
    if attributes.get("season"): features["season"] = attributes["season"]
    if attributes.get("habitat"): features["habitat"] = attributes["habitat"]
    if attributes.get("state") == "цветение": features["flowering"] = True

    url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
    payload = {"species_name": object_nom, "features": features}

    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok or not (await resp.json()).get("images"):
                logger.warning(f"[{user_id}] Изображения для '{object_nom}' с признаками {features} не найдены. Запуск логики fallback.")
                
                # --- [НОВАЯ ЛОГИКА FALLBACK] ---
                if not attributes: # Если изначально не было атрибутов, упрощать нечего
                    return [{"type": "text", "content": f"Извините, я не нашел изображений для «{object_nom}»."}]

                fallback_options = []
                # Проверяем каждый возможный вариант упрощения
                if "season" in attributes:
                    test_features = features.copy(); test_features.pop("season")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "❄️ Без сезона", "callback_data": f"fallback:no_season:{object_nom}"})
                
                if "habitat" in attributes:
                    test_features = features.copy(); test_features.pop("habitat")
                    if await check_simplified_search(session, object_nom, test_features, debug_mode):
                        fallback_options.append({"text": "🌲 Без места", "callback_data": f"fallback:no_habitat:{object_nom}"})

                # Всегда предлагаем вариант "только объект", если были хоть какие-то атрибуты
                if await check_simplified_search(session, object_nom, {}, debug_mode):
                    fallback_options.append({"text": "🖼️ Только объект", "callback_data": f"fallback:basic:{object_nom}"})
                
                if not fallback_options:
                    return [{"type": "text", "content": f"Извините, не нашел изображений для «{object_nom}» с любыми комбинациями признаков."}]

                # Сохраняем исходные атрибуты в Redis, чтобы callback мог их использовать
                context_manager = RedisContextManager()
                fallback_key = f"fallback_attributes:{user_id}"
                await context_manager.set_context(fallback_key, attributes)
                await context_manager.redis_client.expire(fallback_key, 600) # Контекст живет 10 минут
                logger.info(f"[{user_id}] Сохранены атрибуты для fallback: {attributes}")
                
                buttons = [fallback_options[i:i+2] for i in range(0, len(fallback_options), 2)]
                
                return [{"type": "clarification", 
                         "content": f"🖼️ К сожалению, у меня нет точных фотографий для вашего запроса.\n\nДавайте попробуем упростить? Вот что я нашел:",
                         "buttons": buttons}]
                # --- КОНЕЦ ЛОГИКИ FALLBACK ---

            data = await resp.json()
            images = data.get("images", [])
            messages = [{"type": "image", "content": img["image_path"]} for img in images[:5] if isinstance(img, dict) and "image_path" in img]
            
            if not messages:
                 return [{"type": "text", "content": f"Извините, не удалось загрузить ни одного изображения для «{object_nom}»."}]
            return messages

    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_get_picture: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске изображений."}]
    
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

async def handle_get_description(session: aiohttp.ClientSession, analysis: dict, user_id: str, original_query: str, debug_mode: bool) -> list:
    primary_entity = analysis.get("primary_entity", {})
    object_nom = primary_entity.get("name")
    
    # [НОВОЕ] Получаем offset из analysis. Если его нет, по умолчанию 0.
    offset = analysis.get("offset", 0)

    if not object_nom:
        return [{"type": "text", "content": "Не указан объект для поиска описания."}]
        
    find_url = f"{API_URLS['find_species_with_description']}"
    payload = {"name": object_nom, "limit": 4, "offset": offset} # Используем offset в запросе
    logger.debug(f"[{user_id}] Запрос к `find_species_with_description` с payload: {payload}")

    try:
        async with session.post(find_url, json=payload, timeout=DEFAULT_TIMEOUT) as find_resp:
            if not find_resp.ok:
                logger.error(f"[{user_id}] API `find_species` вернул ошибку {find_resp.status} для '{object_nom}'")
                return [{"type": "text", "content": f"Извините, произошла ошибка при поиске '{object_nom}'."}]
            
            data = await find_resp.json()
            status = data.get("status")
            logger.debug(f"[{user_id}] Ответ от `find_species`: status='{status}', matches={data.get('matches')}")

            if status == "ambiguous":
                matches = data.get("matches", [])
                
                # Формируем основные кнопки с вариантами
                buttons = [[{"text": match, "callback_data": f"clarify_object:{match}"}] for match in matches]
                
                # [НОВОЕ] Формируем ряд с системными кнопками
                system_buttons_row = []
                # Добавляем "Любую", если есть хотя бы один вариант
                if matches:
                    system_buttons_row.append({"text": "🎲 Любую", "callback_data": f"clarify_object:{matches[0]}"})

                # Добавляем "Поискать еще", если API сообщил, что есть еще результаты
                if data.get("has_more", False):
                    new_offset = offset + len(matches)
                    # Создаем callback_data для пагинации
                    system_buttons_row.append({"text": "🔍 Поискать еще", "callback_data": f"clarify_more:{object_nom}:{new_offset}"})

                # Если мы сформировали хотя бы одну системную кнопку, добавляем этот ряд в общую клавиатуру
                if system_buttons_row:
                    buttons.append(system_buttons_row)
                
                return [{ "type": "clarification", "content": f"Я знаю несколько видов для «{object_nom}». Уточните, какой именно вас интересует?", "buttons": buttons }]

            elif status == "found":
                # ... (эта часть без изменений)
                canonical_name = data.get("matches", [object_nom])[0]
                desc_url = f"{API_URLS['get_description']}?species_name={canonical_name}&debug_mode={str(debug_mode).lower()}"
                logger.debug(f"[{user_id}] Объект найден: '{canonical_name}'. Запрос описания по URL: {desc_url}")

                async with session.get(desc_url, timeout=DEFAULT_TIMEOUT) as desc_resp:
                    if desc_resp.ok:
                        desc_data = await desc_resp.json()
                        descriptions = desc_data.get("descriptions", [])
                        text = ""
                        
                        if descriptions:
                            first_item = descriptions[0]
                            if isinstance(first_item, dict):
                                text = first_item.get("content", "")
                            elif isinstance(first_item, str):
                                text = first_item
                        
                        if text:
                            logger.info(f"[{user_id}] Описание для '{canonical_name}' успешно найдено и отправлено.")
                            return [{"type": "text", "content": text, "canonical_name": canonical_name}]
            
            # ... (остальная часть функции без изменений)
            logger.warning(f"[{user_id}] Описание для '{object_nom}' не найдено ни на одном из этапов.")
            if get_user_fallback_setting(user_id):
                logger.info(f"[{user_id}] Запускаем GigaChat fallback для запроса: '{original_query}'")
                fallback_answer = await call_gigachat_fallback_service(session, original_query)
                if fallback_answer: 
                    return [{"type": "text", "content": f"**Ответ от GigaChat:**\n\n{fallback_answer}", "parse_mode": "Markdown"}]
            
            return [{"type": "text", "content": f"К сожалению, у меня нет описания для «{object_nom}»."}]

    except Exception as e:
        logger.error(f"[{user_id}] Критическая ошибка в `handle_get_description`: {e}", exc_info=True)
        return [{"type": "text", "content": "Проблема с подключением к серверу описаний."}]
    
async def handle_comparison(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    # TODO: Логика сравнения требует адаптации под новую систему контекста (Шаг 4)
    # Пока что это просто заглушка
    object1 = analysis.get("primary_entity", {}).get("name")
    object2 = analysis.get("secondary_entity", {}).get("name")
    if not object1 or not object2:
        return [{"type": "text", "content": "Недостаточно данных для сравнения."}]
    return [{"type": "text", "content": f"Сравнение между {object1} и {object2} пока в разработке."}]

async def _get_map_from_api(session: aiohttp.ClientSession, url: str, payload: dict, object_name: str, debug_mode: bool, geo_name: str = None) -> list:
    # Эта вспомогательная функция остается почти без изменений
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
            messages.append({"type": "map", "static": map_data["static_map"], "interactive": map_data["interactive_map"], "caption": caption})
        elif caption:
            messages.append({"type": "text", "content": caption})
        return messages
    
async def handle_nearest(session: aiohttp.ClientSession, analysis: dict, debug_mode: bool) -> list:
    # [НОВОЕ] Извлекаем данные из `analysis`
    object_nom = analysis.get("primary_entity", {}).get("name")
    geo_nom = analysis.get("secondary_entity", {}).get("name")
    if not object_nom or not geo_nom:
        return [{"type": "text", "content": "Недостаточно данных для поиска: нужен и объект, и место."}]

    try:
        async with session.post(API_URLS["get_coords"], json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok: return [{"type": "text", "content": f"Не удалось найти координаты для '{geo_nom}'."}]
            coords = await resp.json()

        payload = {"latitude": coords.get("latitude"), "longitude": coords.get("longitude"), "radius_km": 35, "species_name": object_nom, "object_type": "geographical_entity"}
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

            # 1. Формируем базовый текст
            if objects_list:
                caption = f"🗺️ **В районе «{geo_nom}» я нашел {len(objects_list)} биологических объектов.**\n\nХотите увидеть краткий умный обзор или посмотреть полный список?"
            else:
                caption = f"В районе «{geo_nom}» не найдено известных мне биологических объектов."

            # 2. Формируем проактивные кнопки
            buttons = []
            if len(objects_list) > 0:
                buttons.append([
                    {"text": "🎯 Умный обзор", "callback_data": f"explore:overview:{geo_nom}"},
                    {"text": "📋 Полный список", "callback_data": f"explore:full_list:{geo_nom}"}
                ])
            
            # [КЛЮЧЕВОЕ ИЗМЕНЕНИЕ] Добавляем кнопку для интерактивной карты, если она есть
            interactive_map_url = data.get("interactive_map")
            if interactive_map_url:
                buttons.append([
                    {"text": "🌍 Посмотреть на интерактивной карте", "url": interactive_map_url}
                ])

            # 3. Собираем финальный ответ
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
    primary_entity = analysis.get("primary_entity", {})
    secondary_entity = analysis.get("secondary_entity", {})
    
    location_name = secondary_entity.get("name")
    # Если в secondary_entity нет локации (например, запрос "перечисли все заповедники"),
    # то локацией может быть primary_entity, если это GeoPlace.
    if not location_name and primary_entity.get("type") == "GeoPlace":
        location_name = primary_entity.get("name")
    
    # Нормализуем имя основной сущности
    raw_entity_name = primary_entity.get("name")
    canonical_entity_name = normalize_entity_name(raw_entity_name)
    
    # Формируем geo_type в зависимости от результата нормализации
    if canonical_entity_name is None:
        # Это случай "все достопримечательности"
        geo_type_payload = {"primary_type": [""], "specific_types": [""]}
    else:
        # Это случай с конкретным типом (Музеи, Наука и т.д.)
        geo_type_payload = {"primary_type": ["Достопримечательности"], "specific_types": [canonical_entity_name]}
        
    payload = {
        "location_info": { "exact_location": location_name, "region": "", "nearby_places": [] },
        "geo_type": geo_type_payload
    }
    
    url = f"{API_URLS['find_geo_special_description']}?query={original_query}&use_gigachat_answer=true&debug_mode={str(debug_mode).lower()}"
    logger.info(f"Запрос к `find_geo_special_description` с payload: {payload}")
    
    try:
        async with session.post(url, json=payload, timeout=DEFAULT_TIMEOUT) as resp:
            if not resp.ok:
                logger.error(f"API `find_geo_special_description` вернул ошибку {resp.status}. Payload: {payload}")
                return [{"type": "text", "content": "Извините, информация по этому запросу временно недоступна."}]
            
            data = await resp.json()
            answer = data.get("gigachat_answer") or data.get("error", "Не удалось найти информацию.")
            return [{"type": "text", "content": answer}]
            
    except Exception as e:
        logger.error(f"Критическая ошибка в `handle_geo_request`: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске информации."}]
    
async def _update_geo_context(user_id: str, result: dict, original_query: str):
    """Сохраняет географические сущности в контекст пользователя"""
    try:
        from logic.dialogue_manager import DialogueManager
        from utils.context_manager import RedisContextManager
        
        context_manager = RedisContextManager()
        dialogue_manager = DialogueManager(context_manager)
        
        # Определяем категорию объекта (для географических - всегда None)
        object_category = None
        
        # Сохраняем в историю диалога
        await dialogue_manager.update_history(
            user_id=user_id,
            final_intent="get_geo_objects",  # или другой geo intent
            final_entities=result,  # сохраняем ВСЕ сущности
            object_category=object_category,
            original_query=original_query  # ← если добавили это поле
        )
        
        logger.info(f"Контекст обновлен для user_id: {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при обновлении контекста: {e}")
            
    except asyncio.TimeoutError:
        logger.error("API CALL TIMEOUT: Запрос к /object/description/ превысил таймаут.")
        return [{"type": "text", "content": "Сервер достопримечательностей не отвечает. Попробуйте позже."}]
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в handle_geo_request: {e}", exc_info=True)
        return [{"type": "text", "content": "Произошла внутренняя ошибка при поиске информации."}]

async def _process_geo_api_response(data: dict, original_query: str) -> list:
    """Обрабатывает ответ от API географических объектов"""
    
    # Извлекаем ответ от GigaChat
    gigachat_answer = data.get("gigachat_answer")
    error = data.get("error")
    
    if error:
        return [{"type": "text", "content": f"Извините, не удалось найти информацию: {error}"}]
    
    if gigachat_answer:
        # Используем красивый ответ от GigaChat
        return [{"type": "text", "content": gigachat_answer}]
    else:
        # Fallback - если ответа нет
        return [{"type": "text", "content": f"По вашему запросу '{original_query}' информация временно недоступна."}]
    
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
    
    if intent in ["get_geo_objects", "get_geo_info", "get_geo_count"]:
        handler_kwargs.update({
            "user_id": user_id,
            "original_query": original_query
        })

    elif intent == "get_text":
        handler_kwargs.update({"user_id": user_id, "original_query": original_query})
        if "offset" in result:
            handler_kwargs["offset"] = result["offset"]

    handlers = {
        "get_picture": handle_get_picture,
        "get_text": handle_get_description,
        "get_intersection_object_on_map": handle_nearest,
        "get_location": handle_draw_locate_map,
        "get_objects_in_polygon": handle_objects_in_polygon,
        "get_comparison": handle_comparison,

        "get_geo_objects": handle_geo_request,
        "get_geo_info": handle_geo_request,
        "get_geo_count": handle_geo_request
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