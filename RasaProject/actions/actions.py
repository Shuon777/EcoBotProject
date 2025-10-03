from typing import Any, Text, Dict, List, Optional
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction 
import requests
import logging
import json

from logic.slot_validator import handle_known_object_check, normalize_to_nominative
from logic.classify_features import classify_features
from logic.config_rasa import API_URLS, DEFAULT_TIMEOUT, GIGACHAT_TIMEOUT, GIGACHAT_FALLBACK_URL
from logic.settings_manager_rasa import get_user_settings

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_user_fallback_setting(user_id: str) -> bool:
    return get_user_settings(user_id).get("gigachat_fallback", False)

def call_gigachat_fallback_service(question: str) -> Optional[str]:
    url = GIGACHAT_FALLBACK_URL
    logger.debug(f"Обращение к GigaChat Fallback API: {url}")
    try:
        response = requests.post(url, json={"question": question}, timeout=GIGACHAT_TIMEOUT)
        if response.ok:
            logger.info("Fallback-сервис GigaChat ответил успешно.")
            return response.json().get("answer")
        else:
            logger.error(f"Fallback-сервис GigaChat вернул ошибку: {response.status_code} {response.text}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Не удалось подключиться к fallback-сервису GigaChat: {e}")
        return None

def reset_slots_on_error() -> List[Dict[Text, Any]]:
    logger.debug("Сброс слотов из-за ошибки или отсутствия данных.")
    return [SlotSet("object_OFF", None), SlotSet("geo_place", None), SlotSet("feature", None)]

class ActionGetDescription(Action):
    def name(self) -> Text:
        return "action_get_description"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_nom = tracker.get_slot("object_OFF")
        
        if not object_nom:
            dispatcher.utter_message(text="Пожалуйста, уточните, о каком объекте вы спрашиваете.")
            return []
            
        user_id = tracker.sender_id
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)

        logger.debug(f"ActionGetDescription: Получаю описание для объекта '{object_nom}' (без нормализации).")

        try:
            canonical_object = handle_known_object_check(dispatcher, tracker, object_nom)
            if not canonical_object:
                return reset_slots_on_error()
            
            reset_clot = [SlotSet("object_OFF", canonical_object)]
            url = f"{API_URLS['get_description']}?species_name={canonical_object}&debug_mode={str(debug_mode).lower()}"
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            
            data = response.json() if response.ok and response.text else {}
            
            if debug_mode and data.get("debug"):
                dispatcher.utter_message(json_message={"type": "debug", "content": data["debug"]})

            descriptions_list = data.get("descriptions", [])
            text = ""
            if descriptions_list:
                first_item = descriptions_list[0]
                if isinstance(first_item, dict):
                    text = first_item.get("content", "")
                elif isinstance(first_item, str):
                    text = first_item

            if not response.ok or not text:
                logger.warning(f"Описание для '{canonical_object}' не найдено. Проверяем fallback.")
                if get_user_fallback_setting(user_id):
                    dispatcher.utter_message(text="Этого нет в моей базе знаний. Минутку, обращаюсь к GigaChat...")
                    return [FollowupAction("action_execute_gigachat_fallback")]
                else:
                    dispatcher.utter_message(text=f"К сожалению, у меня нет описания для '{canonical_object}'.")
                return reset_slots_on_error()

            dispatcher.utter_message(text=text)

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionGetDescription: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionGetDescription: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
            return reset_slots_on_error()
        
        return reset_clot

class ActionExecuteGigachatFallback(Action):
    def name(self) -> Text:
        return "action_execute_gigachat_fallback"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        question = tracker.latest_message.get('text')
        raw_object = tracker.get_slot("object_OFF")
        gigachat_answer = call_gigachat_fallback_service(question)
        
        if gigachat_answer:
            full_answer = f"**Ответ от GigaChat:**\n\n{gigachat_answer}"
            dispatcher.utter_message(json_message={"text": full_answer, "parse_mode": "Markdown"})
            return [] 
        else:
            dispatcher.utter_message(text=f"Извините, не удалось получить дополнительную информацию для '{raw_object}'.")
            return reset_slots_on_error()

class ActionGetPic(Action):
    def name(self) -> Text:
        return "action_get_picture"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_nom = tracker.get_slot("object_OFF")
        raw_feats = tracker.get_slot("feature") or [] 
        features = classify_features(raw_feats)
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)
        
        try:
            canonical_object = handle_known_object_check(dispatcher, tracker, object_nom)
            if not canonical_object:
                return reset_slots_on_error()
            
            reset_clot = [SlotSet("object_OFF", canonical_object)]
            url = f"{API_URLS['search_images']}?debug_mode={str(debug_mode).lower()}"
            payload = {"species_name": canonical_object}
            if features:
                payload["features"] = features
            
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
            data = response.json()

            if debug_mode:
                # В режиме отладки отправляем весь JSON ответа
                dispatcher.utter_message(json_message=data)
                return []

            if not response.ok or data.get("status") == "not_found" or not data.get("images"):
                dispatcher.utter_message(text=f"Извините, я не нашел изображений для '{canonical_object}'.")
                return reset_slots_on_error()

            images = data.get("images", [])
            sent_images_count = 0
            for image_item in images[:5]:
                if isinstance(image_item, dict) and "image_path" in image_item:
                    image_url = image_item["image_path"]
                    try:
                        check_resp = requests.head(image_url, timeout=5, allow_redirects=True)
                        if check_resp.status_code == 200:
                            dispatcher.utter_message(image=image_url)
                            sent_images_count += 1
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Не удалось проверить URL изображения {image_url}: {e}")
            
            if sent_images_count == 0:
                dispatcher.utter_message(text=f"Извините, не удалось загрузить ни одного изображения для '{canonical_object}'.")
                return reset_slots_on_error()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionGetPic: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionGetPic: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так.")
            return reset_slots_on_error()
        return reset_clot

class ActionNeasrest(Action):
    def name(self) -> Text:
        return "action_neasrest"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_nom = tracker.get_slot("object_OFF")
        geo_nom = tracker.get_slot("geo_place")
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)

        try:
            canonical_object = handle_known_object_check(dispatcher, tracker, object_nom)
            if not canonical_object:
                return reset_slots_on_error()
            
            reset_clot = [SlotSet("object_OFF", canonical_object)]
        
            coords_url = API_URLS["get_coords"]
            coords_response = requests.post(coords_url, json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT)
            
            if not coords_response.ok or coords_response.json().get("status") == "not_found":
                dispatcher.utter_message(text=f"Извините, не удалось найти координаты для места '{geo_nom}'.")
                return reset_slots_on_error()

            coords_data = coords_response.json()
            map_url = f"{API_URLS['coords_to_map']}?debug_mode={str(debug_mode).lower()}"
            map_payload = {
                "latitude": coords_data.get("latitude"), "longitude": coords_data.get("longitude"), 
                "radius_km": 35, "species_name": canonical_object, "object_type": "geographical_entity"
            }
            map_response = requests.post(map_url, json=map_payload, timeout=DEFAULT_TIMEOUT)
            map_data = map_response.json()
            
            if debug_mode and map_data.get("debug"):
                dispatcher.utter_message(json_message={"type": "debug", "content": map_data["debug"]})

            if not map_response.ok:
                dispatcher.utter_message(text="Не удалось построить карту для этого места.")
                return reset_slots_on_error()

            names = map_data.get("names", [])
            unique_names = sorted(list(set(name.capitalize() for name in names)))

            if unique_names:
                message_text = (f"📍 Рядом с '{geo_nom}' вы можете встретить '{canonical_object}' в следующих местах:\n" + "• " + "\n• ".join(unique_names))
                dispatcher.utter_message(text=message_text)
            
            if map_data.get("status") == "no_objects":
                dispatcher.utter_message(text=f"К сожалению, я не нашел '{canonical_object}' поблизости от '{geo_nom}'.")
            
            if map_data.get("interactive_map") and map_data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Перейти на карту", "url": map_data["interactive_map"]}]]}})
            elif not unique_names:
                return reset_slots_on_error()

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionNeasrest: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionNeasrest: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так.")
            return reset_slots_on_error()
        return reset_clot
    
class ActionDrawLocateMap(Action):
    def name(self) -> Text:
        return "action_draw_locate_map"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_nom = tracker.get_slot("object_OFF")
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)

        try:
            canonical_object = handle_known_object_check(dispatcher, tracker, object_nom)
            if not canonical_object:
                return reset_slots_on_error()
            
            reset_clot = [SlotSet("object_OFF", canonical_object)]
            url = f"{API_URLS['coords_to_map']}?debug_mode={str(debug_mode).lower()}"
            payload = {
                "latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, 
                "species_name": canonical_object, "object_type": "geographical_entity"
            }
            map_response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
            map_data = map_response.json()

            if debug_mode and map_data.get("debug"):
                dispatcher.utter_message(json_message={"type": "debug", "content": map_data["debug"]})

            if not map_response.ok:
                dispatcher.utter_message(text="Не удалось построить карту ареала.")
                return reset_slots_on_error()
            
            if map_data.get("status") == "no_objects":
                dispatcher.utter_message(text=f"К сожалению, я не смог найти ареал обитания для '{canonical_object}'.")
                return reset_slots_on_error()

            names = map_data.get("names", [])
            unique_names = sorted(list(set(name.capitalize() for name in names)))
            if unique_names:
                message_text = (f"📍 '{canonical_object.capitalize()}' встречается в следующих местах:\n" + "• " + "\n• ".join(unique_names))
                dispatcher.utter_message(text=message_text)

            if map_data.get("interactive_map") and map_data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Показать ареал на карте", "url": map_data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionDrawLocateMap: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionDrawLocateMap: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так.")
            return reset_slots_on_error()
        return reset_clot

class ActionObjectsInPolygon(Action):
    def name(self) -> Text:
        return "action_objects_in_polygon"
        
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        geo_nom = tracker.get_slot("geo_place")
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)

        try:
            url = f"{API_URLS['objects_in_polygon']}?debug_mode={str(debug_mode).lower()}"
            payload = {"name": geo_nom, "buffer_radius_km": 5, "object_type": "biological_entity"}
            response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
            data = response.json()
            
            if debug_mode and data.get("debug"):
                dispatcher.utter_message(json_message={"type": "debug", "content": data["debug"]})

            if not response.ok:
                 dispatcher.utter_message(text=f"Не удалось найти полигон для места '{geo_nom}'.")
                 return reset_slots_on_error()

            unique_names = sorted(list(set(name.capitalize() for name in data.get("all_biological_names", []))))
            
            if unique_names:
                flora_list = f"🌿 В районе '{geo_nom}' найдены следующие объекты:\n" + "• " + "\n• ".join(unique_names)
                dispatcher.utter_message(text=flora_list)
            else:
                dispatcher.utter_message(text=f"В районе '{geo_nom}' не найдено известных мне объектов.")

            if data.get("interactive_map") and data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": data["static_map"], "reply_markup": { "inline_keyboard": [[{"text": "🌍 Показать на карте", "url": data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionObjectsInPolygon: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionObjectsInPolygon: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так.")
            return reset_slots_on_error()
        return []

class ActionAskEcoBot(Action):
    def name(self) -> Text:
        return "action_ask_ecobot"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_nom = tracker.get_slot("object_OFF")
        if not object_nom:
            dispatcher.utter_message(text="К сожалению, я не имею информацию об объекте, о котором вы спрашиваете.")
            return []
        if not handle_known_object_check(dispatcher, tracker, object_nom):
            return reset_slots_on_error()

        question = tracker.latest_message.get("text")
        debug_mode = tracker.latest_message.get("metadata", {}).get("debug_mode", False)
        try:
            payload = {
                "question": question, "knowledge_base_type": "vector", "similarity_threshold": 0.2, 
                "similarity_deviation": 0.025, "use_gigachat": True, "strict_filter": True,
                "debug_mode": debug_mode
            }
            response = requests.post(API_URLS["ask_ecobot"], json=payload, timeout=DEFAULT_TIMEOUT)
            data = response.json()

            if debug_mode and data.get("debug_info"):
                dispatcher.utter_message(json_message={"type": "debug", "content": data["debug_info"]})

            if not response.ok:
                dispatcher.utter_message(text="Произошла ошибка при обращении к базе знаний.")
                return reset_slots_on_error()

            dispatcher.utter_message(text=data.get("answer", "Ответ не найден."))
            
            for url in data.get("multi_url", {}).get("image_urls", []):
                dispatcher.utter_message(image=url)
            for url in data.get("multi_url", {}).get("file_urls", []):
                dispatcher.utter_message(attachment={"type": "file", "payload": {"url": url}})
            
            geo_places = data.get("multi_url", {}).get("geo_places", [])
            map_response = None
            if "рядом" in question.lower() and len(geo_places) >= 2:
                map_response = requests.post(API_URLS["get_species_area"], json={"center": geo_places[0], "region": geo_places[1]}, timeout=GIGACHAT_TIMEOUT)
            elif len(geo_places) >= 1:
                map_response = requests.post(API_URLS["draw_multiple_places"], json={"geo_places": geo_places}, timeout=GIGACHAT_TIMEOUT)

            if map_response and map_response.ok:
                map_data = map_response.json()
                if map_data.get("interactive_map") and map_data.get("static_map"):
                    dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Перейти на карту", "url": map_data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionAskEcoBot: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionAskEcoBot: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так.")
            return reset_slots_on_error()
        return []

class ActionClearSlots(Action):
    def name(self) -> str:
        return "action_clear_slots"
    def run(self, dispatcher, tracker, domain):
        logger.debug("Очистка слотов по команде.")
        return [SlotSet("object_OFF", None), SlotSet("feature", None), SlotSet("geo_place", None)]

class ActionShowSignQQuestions(Action):
    def name(self) -> Text:
        return "action_show_signq_questions"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        buttons = [
            [{"text": "Где можно встретить Ольхонскую полевку рядом с поселком Култук"}],
            [{"text": "Покажи пихту сибирскую зимой"}],
            [{"text": "Покажи Сибирский кедр вдоль берега Байкала"}],
            [{"text": "Покажи шишку сибирской сосны на ветке"}],
            [{"text": "⬅️ Выйти в меню"}]
        ]
        dispatcher.utter_message(text="Выберите вопрос с признаком:", custom={"reply_markup": {"keyboard": buttons, "resize_keyboard": True}})
        return []

class ActionShowSimpleQuestions(Action):
    def name(self) -> Text:
        return "action_show_simple_questions"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        buttons = [
            [{"text": "Покажи где растет Копеечник зундукский"}],
            [{"text": "Расскажи о Байкальской нерпе"}],
            [{"text": "Как выглядит даурский ёж"}],
            [{"text": "Покажи цветение Синюхи мохнатой"}],
            [{"text": "Какую флору я могу встретить на Байкальской тропе"}],
            [{"text": "⬅️ Выйти в меню"}]
        ]
        dispatcher.utter_message(text="Выберите вопрос:", custom={"reply_markup": {"keyboard": buttons, "resize_keyboard": True}})
        return []

class ActionShowBBW(Action):
    def name(self) -> Text:
        return "action_show_bbw"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        buttons = [[{"text": "📝 Перейти на подробную статью", "url": "https://testecobot.ru/maps/location/BBW/index.html"}]]
        dispatcher.utter_message(text="🔎 Материалы по вашему запросу", custom={"reply_markup": {"inline_keyboard": buttons}})
        return []

class ActionShowQuestionsButtons(Action):
    def name(self) -> Text:
        return "action_show_questions_buttons"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        buttons = [
            [{"text": "Простые вопросы"}],
            [{"text": "Вопросы с признаком"}],
            [{"text": "⬅️ Выйти в меню"}]
        ]
        dispatcher.utter_message(text="Выберите тип вопроса", custom={"reply_markup": {"keyboard": buttons, "resize_keyboard": True}})
        return []

class ActionShowHomeMenu(Action):
    def name(self) -> Text:
        return "action_show_home_menu"
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(text="Чем еще могу помочь?")
        return []
    
class ActionDisambiguateDescription(Action):
    def name(self) -> Text:
        return "action_disambiguate_description"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        object_name = tracker.get_slot("object_OFF")
        
        if not object_name:
            dispatcher.utter_message(text="Пожалуйста, уточните, о каком объекте вы спрашиваете.")
            return []

        # Определяем, новый ли это поиск или пагинация
        current_offset = int(tracker.get_slot("search_offset") or 0)
        if tracker.latest_message['intent'].get('name') != 'search_more':
            current_offset = 0

        logger.debug(f"Запрос на уточнение для '{object_name}' со смещением {current_offset}")

        try:
            response = requests.post(
                API_URLS["find_species_with_description"], 
                json={"name": object_name, "limit": 4, "offset": current_offset}
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.error(f"Ошибка API при уточнении объекта: {e}")
            dispatcher.utter_message(text="Извините, возникла проблема с подключением к базе знаний.")
            return [SlotSet("search_offset", None)]

        status = data.get("status")
        matches = data.get("matches", [])

        # Если API нашло точное совпадение (например, для "Ива козья")
        if status == "found":
            logger.debug(f"Найдено точное совпадение для '{object_name}'. Сразу запускаем action_get_description.")
            return [FollowupAction("action_get_description")]

        # Если API нашло несколько вариантов (для "ива")
        elif status == "ambiguous":
            logger.debug(f"Найдено несколько вариантов для '{object_name}'. Показываем кнопки.")
            
            buttons = [{"title": name, "payload": f'/select_option{{"index": {i}}}'} for i, name in enumerate(matches)]
            if data.get("has_more", False):
                buttons.append({"title": "Поискать еще ➡️", "payload": "/search_more"})

            inline_keyboard = [[{"text": b["title"], "callback_data": b["payload"]}] for b in buttons]
            custom_json = {"text": "Я знаю несколько видов. Уточните, какой именно вас интересует?", "reply_markup": {"inline_keyboard": inline_keyboard}}
            dispatcher.utter_message(json_message=custom_json)
            
            new_offset = current_offset + len(matches)
            return [SlotSet("disambiguation_options", matches), SlotSet("search_offset", new_offset)]

        # Если ничего не найдено
        else: # status == "not_found"
            dispatcher.utter_message(text=f"К сожалению, у меня нет описания для '{object_name}'.")
            return [SlotSet("search_offset", None)]

class ActionClearSearchOffset(Action):
    def name(self) -> Text:
        return "action_clear_search_offset"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        logger.debug("Очистка слота search_offset.")
        return [SlotSet("search_offset", None)]
    
class ActionRetrieveSelection(Action):
    def name(self) -> Text:
        return "action_retrieve_selection"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        logger.debug("--- ActionRetrieveSelection ЗАПУЩЕН ---")

        # Способ 1: Получаем все сущности
        all_entities = tracker.latest_message.get('entities', [])
        logger.debug(f"Все сущности, которые я вижу: {all_entities}")

        # Способ 2: Используем стандартный метод get_latest_entity_values
        selected_index_str = next(tracker.get_latest_entity_values("index"), None)
        logger.debug(f"Результат get_latest_entity_values('index'): {selected_index_str}")
        
        if selected_index_str is None:
            logger.error("КРИТИЧЕСКАЯ ОШИБКА: Не удалось извлечь 'index' из трекера. Проверьте domain.yml и NLU.")
            dispatcher.utter_message(text="Произошла ошибка при обработке вашего выбора. Попробуйте еще раз.")
            return []

        options = tracker.get_slot("disambiguation_options")
        logger.debug(f"Опции, которые я достал из слота: {options}")
        
        if not options:
            logger.error("КРИТИЧЕСКАЯ ОШИБКА: Слот 'disambiguation_options' пуст.")
            dispatcher.utter_message(text="Извините, я, кажется, забыл, из чего мы выбирали. Давайте начнем заново.")
            return []

        try:
            selected_index = int(selected_index_str)
            selected_option = options[selected_index]
            
            logger.debug(f"Успех! Пользователь выбрал индекс {selected_index}, что соответствует '{selected_option}'")
            
            return [
                SlotSet("object_OFF", selected_option), 
                SlotSet("disambiguation_options", None)
            ]
        except (ValueError, IndexError) as e:
            logger.error(f"Не удалось получить вариант по индексу '{selected_index_str}': {e}")
            dispatcher.utter_message(text="Произошла ошибка при выборе варианта. Пожалуйста, попробуйте снова.")
            return []