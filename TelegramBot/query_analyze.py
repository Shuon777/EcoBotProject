import os
import json
import logging
from typing import Dict, Any
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()

class QueryAnalyzer:
    def __init__(self):
        """Инициализация обработчика запроса пользователя"""
        try:
            self.llm = self._init_gigachat()
            print("GigaChat успешно инициализирован")
        except Exception as e:
            logger.error(f"Ошибка инициализации GigaChat: {str(e)}")
            raise

    def _init_gigachat(self) -> GigaChat:
        """Инициализация GigaChat с API-ключом"""
        try:
            api_key = os.getenv('SBER_KEY_ENTERPRICE')
            if not api_key:
                raise ValueError("API ключ не найден в переменных окружения")

            return GigaChat(
                credentials=api_key,
                model="GigaChat-2-Max",
                verify_ssl_certs=False,
                profanity_check=False,
                timeout=120,
                scope="GIGACHAT_API_CORP"
            )
        except Exception as e:
            logger.error(f"Ошибка создания GigaChat инстанса: {str(e)}")
            raise

    def _get_analyzer_prompt(self) -> ChatPromptTemplate:
        analyzer_system_prompt = """## РОЛЕВАЯ МОДЕЛЬ:
    Ты — интеллектуальный парсер запросов о флоре и фауне Байкала.
    Анализируй текст запроса и возвращай результат строго в формате JSON.

    ВАЖНЫЕ ПРАВИЛА:
    1) Всегда возвращай только корректный JSON. Никаких пояснений вне JSON.
    2) Если не можешь распознать запрос — верни:
    {{
    "intent": "unknown",
    "object": null,
    "geo_place": null,
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": false
    }}
    3) Поле features — всегда JSON-объект (словарь). Если признаков нет — {{}}
    4) Разрешённые ключи в features: season, habitat, cloudiness, fauna_type, flora_type, location, flowering, fruits_present, author, date
    5) Любые другие признаки (например «на фоне …», «рядом с памятником …», «с телефоном в лапе») НЕ включай в features. Их нужно возвращать в unsupported_features
    6) Если unsupported_features не пуст, can_fulfill = false. Не пытайся заменять неподдерживаемые признаки на другие
    7) Если в запросе есть реальный топоним (город, село, парк, бухта), укажи его в geo_place
    8) Перед тем как включить object в JSON: исправь опечатки и приведи к именительному падежу. Пример: "чарбец" → "чабрец"

    ПОЛЯ JSON:
    1. intent — намерение пользователя, одно из значений:
    - get_picture — показать картинку объекта
    - get_location — показать карту ареала обитания объекта
    - get_intersection_object_on_map — показать места обитания объекта возле какого-то места
    - get_text — получить фактологическое описание объекта
    - get_objects_in_polygon — узнать, что обитает или растёт в каком-то месте
    - unknown — если определить намерение невозможно
    2. object — название объекта флоры или фауны Байкала (например "пихта сибирская"), либо null
    3. geo_place — географическое место или регион (например "Култук", "Малое море"), либо null
    4. features — словарь поддерживаемых признаков. Возможные ключи:
    - season — время года (Зима, Весна, Лето, Осень)
    - habitat — среда обитания (Лес, Поле, Луг, Горы, Озеро, Городская среда, Побережье и др.)
    - cloudiness — облачность (Ясно, Переменная облачность, Пасмурно)
    - fauna_type — тип животного (Млекопитающее, Птица, Рыба, Насекомое и др.)
    - flora_type — тип растения (Дерево, Кустарник, Трава, Цветущее растение, Мох и др.)
    - location — регион или страна
    - flowering — цветёт или нет (Да/Нет)
    - fruits_present — если есть в запросе слова "шишка", "желудь" и тому подобноее, то передаем название плода в JSON, иначе передаем "Нет"
    - author — автор фотографии
    - date — дата съёмки
    5. unsupported_features — список строк с неподдерживаемыми фразами из запроса (например "на фоне колеса обозрения"). Если таких нет — []
    6. can_fulfill — true/false. Ставь false, если есть unsupported_features

    ФОРМАТ ОТВЕТА:
    Отвечай строго в JSON без пояснений.

    ПРИМЕРЫ:

    Пример 0.
    Ввод: Покажи шишку лиственницы сибирской
    Вывод:
    {{
    "intent": "get_picture",
    "object": "лиственница сибирская",
    "geo_place": null,
    "features": {{
        "fruits_present": "Шишка",
    }},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 1.
    Ввод: Как выглядит эдельвейс?
    Вывод:
    {{
    "intent": "get_picture",
    "object": "эдельвейс",
    "geo_place": null,
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 2.
    Ввод: Покажи где растет синюха мохнатая
    Вывод:
    {{
    "intent": "get_location",
    "object": "синюха мохнатая",
    "geo_place": null,
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 3.
    Ввод: Где можно встретить Ольхонскую полевку рядом с Култуком
    Вывод:
    {{
    "intent": "get_intersection_object_on_map",
    "object": "Ольхонская полевка",
    "geo_place": "Култук",
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 4.
    Ввод: Расскажи о пихте сибирской
    Вывод:
    {{
    "intent": "get_text",
    "object": "пихта сибирская",
    "geo_place": null,
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 5.
    Ввод: Какую флору я могу встретить на Малом море
    Вывод:
    {{
    "intent": "get_objects_in_polygon",
    "object": null,
    "geo_place": "Малое море",
    "features": {{}},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 6.
    Ввод: Покажи мне фото белки зимой в лесу
    Вывод:
    {{
    "intent": "get_picture",
    "object": "белка",
    "geo_place": null,
    "features": {{
        "season": "Зима",
        "habitat": "Лес"
    }},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 7.
    Ввод: Где летом растет шиповник
    Вывод:
    {{
    "intent": "get_location",
    "object": "шиповник",
    "geo_place": null,
    "features": {{
        "season": "Лето",
        "flora_type": "Кустарник"
    }},
    "unsupported_features": [],
    "can_fulfill": true
    }}

    Пример 8. (неподдерживаемый признак "на фоне …")
    Ввод: Покажи чайку на фоне колеса обозрения
    Вывод:
    {{
    "intent": "get_picture",
    "object": "чайка",
    "geo_place": null,
    "features": {{}},
    "unsupported_features": ["на фоне колеса обозрения"],
    "can_fulfill": false
    }}
    """
        return ChatPromptTemplate.from_messages([
            ("system", analyzer_system_prompt),
            ("human", "{query}")
        ])

    def query_analyzer(self, query: str) -> Dict[str, Any]:
        """Анализирует текст запроса и возвращает JSON с intent, object, geo_place и features"""
        try:
            print(f"Начало анализа для запроса: {query}")

            prompt = self._get_analyzer_prompt()
            chain = prompt | self.llm
            response = chain.invoke({"query": query})

            generated_text = response.content.strip()
            print(f"Сгенерирован ответ: {generated_text}")

            try:
                # Очистка JSON от возможных оберток
                if generated_text.startswith("```json"):
                    generated_text = generated_text[7:]
                if generated_text.endswith("```"):
                    generated_text = generated_text[:-3]

                parsed_json = json.loads(generated_text.strip())

                # features — всегда dict
                features = parsed_json.get("features")
                if features is None:
                    parsed_json["features"] = {}
                elif isinstance(features, list):
                    merged = {}
                    for item in features:
                        if isinstance(item, dict):
                            merged.update(item)
                    parsed_json["features"] = merged
                elif not isinstance(features, dict):
                    parsed_json["features"] = {}

                # unsupported_features — всегда list
                uf = parsed_json.get("unsupported_features")
                if uf is None:
                    parsed_json["unsupported_features"] = []
                elif not isinstance(uf, list):
                    parsed_json["unsupported_features"] = [str(uf)]

                # can_fulfill — bool, по умолчанию True, но если есть unsupported_features — False (если модель не выставила)
                if "can_fulfill" not in parsed_json:
                    parsed_json["can_fulfill"] = len(parsed_json["unsupported_features"]) == 0
                else:
                    parsed_json["can_fulfill"] = bool(parsed_json["can_fulfill"])


                print("Сгенерированный JSON успешно распарсен")
                logging.info(f"{parsed_json}")
                return {
                    "success": True,
                    "result": parsed_json
                }
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON: {str(e)}")
                return {
                    "success": False,
                    "error": f"Невалидный JSON: {str(e)}",
                    "raw_text": generated_text
                }

        except Exception as e:
            logger.error(f"Ошибка анализа запроса: {str(e)}", exc_info=True)
            return {
                "success": False,
                "error": f"Ошибка анализа: {str(e)}"
            }

