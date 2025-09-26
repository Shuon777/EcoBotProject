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
        raw_object = tracker.get_slot("object_OFF")
        if not raw_object:
            dispatcher.utter_message(text="Пожалуйста, уточните, о каком объекте вы спрашиваете.")
            return []
            
        object_nom = normalize_to_nominative(raw_object)
        user_id = tracker.sender_id

        try:
            url = f"{API_URLS['get_description']}?species_name={object_nom}"
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            
            data = response.json() if response.ok and response.text else {}
            descriptions_list = data.get("descriptions", [])
            
            text = ""
            if descriptions_list:
                first_item = descriptions_list[0]
                if isinstance(first_item, dict):
                    text = first_item.get("content", "")
                elif isinstance(first_item, str):
                    text = first_item

            if not response.ok or not text:
                logger.warning(f"Описание для '{object_nom}' не найдено. Проверяем fallback.")
                if get_user_fallback_setting(user_id):
                    dispatcher.utter_message(text="Этого нет в моей базе знаний. Минутку, обращаюсь к GigaChat...")
                    return [FollowupAction("action_execute_gigachat_fallback")]
                else:
                    dispatcher.utter_message(text=f"К сожалению, у меня нет описания для '{raw_object}'.")
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
        
        return []

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
        raw_object = tracker.get_slot("object_OFF")
        object_nom = normalize_to_nominative(raw_object)
        raw_feats = tracker.get_slot("feature") or [] 
        features = classify_features(raw_feats)
        # dispatcher.utter_message(text=f"Сущности: {tracker.latest_message['entities']}")
        try:
            payload = {"species_name": object_nom}
            if features:
                payload["features"] = features
            
            url = API_URLS["search_images"]
            logger.debug(f"Обращение к API: {url} с телом: {payload}")
            response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT)
            
            if not response.ok:
                dispatcher.utter_message(text=f"Извините, я не нашел изображений для '{raw_object}'.")
                return reset_slots_on_error()

            data = response.json()
            images = data.get("images", [])
            if data.get("status") == "not_found" or not images:
                dispatcher.utter_message(text=f"Извините, я не нашел изображений для '{raw_object}'.")
                return reset_slots_on_error()

            sent_images_count = 0
            for image_item in images[:5]:
                if isinstance(image_item, dict) and "image_path" in image_item:
                    image_url = image_item["image_path"]
                    try:
                        check_resp = requests.head(image_url, timeout=5, allow_redirects=True)
                        if check_resp.status_code == 200:
                            dispatcher.utter_message(image=image_url)
                            sent_images_count += 1
                        else:
                            logger.warning(f"URL изображения вернул статус {check_resp.status_code}: {image_url}")
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Не удалось проверить URL изображения {image_url}: {e}")
            
            if sent_images_count == 0:
                dispatcher.utter_message(text=f"Извините, не удалось загрузить ни одного изображения для '{raw_object}'.")
                return reset_slots_on_error()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionGetPic: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionGetPic: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
            return reset_slots_on_error()
        return []

class ActionNeasrest(Action):
    def name(self) -> Text:
        return "action_neasrest"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        raw_object = tracker.get_slot("object_OFF")
        raw_geo_place = tracker.get_slot("geo_place")
        object_nom = normalize_to_nominative(raw_object)
        geo_nom = normalize_to_nominative(raw_geo_place)

        try:
            if not handle_known_object_check(dispatcher, tracker, object_nom):
                return reset_slots_on_error()
        
            coords_url = API_URLS["get_coords"]
            coords_response = requests.post(coords_url, json={"name": geo_nom}, timeout=DEFAULT_TIMEOUT)
            
            if not coords_response.ok or coords_response.json().get("status") == "not_found":
                dispatcher.utter_message(text=f"Извините, не удалось найти координаты для места '{raw_geo_place}'.")
                return reset_slots_on_error()

            coords_data = coords_response.json()
            map_payload = {
                "latitude": coords_data.get("latitude"),
                "longitude": coords_data.get("longitude"),
                "radius_km": 35,
                "species_name": object_nom,
                "object_type": "geographical_entity"
            }
            map_response = requests.post(API_URLS["coords_to_map"], json=map_payload, timeout=DEFAULT_TIMEOUT)
            
            if not map_response.ok:
                dispatcher.utter_message(text="Не удалось построить карту для этого места.")
                return reset_slots_on_error()

            map_data = map_response.json()
            names = map_data.get("names", [])
            unique_names = sorted(list(set(name.capitalize() for name in names)))

            if unique_names:
                message_text = (f"📍 Рядом с '{raw_geo_place}' вы можете встретить '{raw_object}' в следующих местах:\n" + "• " + "\n• ".join(unique_names))
                dispatcher.utter_message(text=message_text)
            
            if map_data.get("status") == "no_objects":
                dispatcher.utter_message(text=f"К сожалению, я не нашел '{raw_object}' поблизости от '{raw_geo_place}'.")
            
            if map_data.get("interactive_map") and map_data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Перейти на карту", "url": map_data["interactive_map"]}]]}})
            elif not unique_names:
                return reset_slots_on_error()

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionNeasrest: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionNeasrest: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
            return reset_slots_on_error()

        return []
    
class ActionDrawLocateMap(Action):
    def name(self) -> Text:
        return "action_draw_locate_map"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        raw_object = tracker.get_slot("object_OFF")
        object_nom = normalize_to_nominative(raw_object)

        try:
            if not handle_known_object_check(dispatcher, tracker, object_nom):
                return reset_slots_on_error()

            payload = {
                "latitude": 53.27612, "longitude": 107.3274, "radius_km": 500000, 
                "species_name": object_nom, "object_type": "geographical_entity"
            }
            map_response = requests.post(API_URLS["coords_to_map"], json=payload, timeout=DEFAULT_TIMEOUT)
            
            if not map_response.ok:
                dispatcher.utter_message(text="Не удалось построить карту ареала.")
                return reset_slots_on_error()

            map_data = map_response.json()
            
            if map_data.get("status") == "no_objects":
                dispatcher.utter_message(text=f"К сожалению, я не смог найти ареал обитания для '{raw_object}'.")
                return reset_slots_on_error()

            names = map_data.get("names", [])
            unique_names = sorted(list(set(name.capitalize() for name in names)))
            if unique_names:
                message_text = (f"📍 '{raw_object.capitalize()}' встречается в следующих местах:\n" + "• " + "\n• ".join(unique_names))
                dispatcher.utter_message(text=message_text)

            if map_data.get("interactive_map") and map_data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Показать ареал на карте", "url": map_data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionDrawLocateMap: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionDrawLocateMap: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
            return reset_slots_on_error()

        return []

class ActionObjectsInPolygon(Action):
    def name(self) -> Text:
        return "action_objects_in_polygon"
        
    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        raw_geo_place = tracker.get_slot("geo_place")
        geo_nom = normalize_to_nominative(raw_geo_place)

        try:
            payload = {"name": geo_nom, "buffer_radius_km": 5, "object_type": "biological_entity"}
            response = requests.post(API_URLS["objects_in_polygon"], json=payload, timeout=DEFAULT_TIMEOUT)

            if not response.ok:
                 dispatcher.utter_message(text=f"Не удалось найти полигон для места '{raw_geo_place}'. Пожалуйста, уточните название.")
                 return reset_slots_on_error()

            data = response.json()
            unique_names = sorted(list(set(name.capitalize() for name in data.get("all_biological_names", []))))
            
            if unique_names:
                flora_list = f"🌿 В районе '{raw_geo_place}' найдены следующие объекты:\n" + "• " + "\n• ".join(unique_names)
                dispatcher.utter_message(text=flora_list)
            else:
                dispatcher.utter_message(text=f"В районе '{raw_geo_place}' не найдено известных мне объектов.")

            if data.get("interactive_map") and data.get("static_map"):
                dispatcher.utter_message(json_message={"photo": data["static_map"], "reply_markup": { "inline_keyboard": [[{"text": "🌍 Показать на карте", "url": data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionObjectsInPolygon: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionObjectsInPolygon: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
            return reset_slots_on_error()

        return []

class ActionAskEcoBot(Action):
    def name(self) -> Text:
        return "action_ask_ecobot"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        raw_object = tracker.get_slot("object_OFF")
        if not raw_object:
            dispatcher.utter_message(text="К сожалению, я не имею информацию об объекте, о котором вы спрашиваете.")
            return []

        object_nom = normalize_to_nominative(raw_object)
        if not handle_known_object_check(dispatcher, tracker, object_nom):
            return reset_slots_on_error()

        question = tracker.latest_message.get("text")
        try:
            payload = {
                "question": question, 
                "knowledge_base_type": "vector", 
                "similarity_threshold": 0.2, 
                "similarity_deviation": 0.025, 
                "use_gigachat": True, 
                "strict_filter": True
            }
            response = requests.post(API_URLS["ask_ecobot"], json=payload, timeout=DEFAULT_TIMEOUT)

            if not response.ok:
                dispatcher.utter_message(text="Произошла ошибка при обращении к базе знаний.")
                return reset_slots_on_error()

            data = response.json()
            dispatcher.utter_message(text=data.get("answer", "Ответ не найден."))
            
            for url in data.get("multi_url", {}).get("image_urls", []):
                dispatcher.utter_message(image=url)
            for url in data.get("multi_url", {}).get("file_urls", []):
                dispatcher.utter_message(attachment={"type": "file", "payload": {"url": url}})
            
            geo_places = data.get("multi_url", {}).get("geo_places", [])
            map_response = None
            if "рядом" in question.lower() and len(geo_places) >= 2:
                url_area = API_URLS["get_species_area"]
                map_response = requests.post(url_area, json={"center": geo_places[0], "region": geo_places[1]}, timeout=GIGACHAT_TIMEOUT)
            elif len(geo_places) >= 1:
                url_draw = API_URLS["draw_multiple_places"]
                map_response = requests.post(url_draw, json={"geo_places": geo_places}, timeout=GIGACHAT_TIMEOUT)

            if map_response and map_response.ok:
                map_data = map_response.json()
                if map_data.get("interactive_map") and map_data.get("static_map"):
                    dispatcher.utter_message(json_message={"photo": map_data["static_map"], "reply_markup": {"inline_keyboard": [[{"text": "🌍 Перейти на карту", "url": map_data["interactive_map"]}]]}})

        except requests.exceptions.RequestException as e:
            logger.error(f"Сетевая ошибка в ActionAskEcoBot: {e}", exc_info=True)
            dispatcher.utter_message(text="Проблема с подключением к серверу. Попробуйте, пожалуйста, позже.")
            return reset_slots_on_error()
        except Exception as e:
            logger.error(f"Непредвиденная ошибка в ActionAskEcoBot: {e}", exc_info=True)
            dispatcher.utter_message(text="Ой, что-то пошло не так. Попробуйте еще раз.")
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
