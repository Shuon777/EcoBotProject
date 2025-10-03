# --- НАЧАЛО ФАЙЛА: TelegramBot/query_analyze.py ---

import os
import json
import logging
from typing import Dict, Any, Optional
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
            logger.info("GigaChat успешно инициализирован.")
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
    
    def _get_category_detection_prompt(self) -> ChatPromptTemplate:
        """Создает промпт для быстрой классификации объекта."""
        category_system_prompt = """
    Твоя задача — определить общую биологическую категорию для указанного объекта.
    Ответь ТОЛЬКО ОДНИМ СЛОВОМ из предложенного списка. Никаких пояснений.

    ## ВОЗМОЖНЫЕ КАТЕГОРИИ:
    - Дерево
    - Кустарник
    - Трава
    - Млекопитающее
    - Птица
    - Рыба
    - Насекомое
    - Другое

    ## ПРИМЕРЫ:
    - Ввод: "лиственница сибирская" -> Вывод: Дерево
    - Ввод: "байкальская нерпа" -> Вывод: Млекопитающее
    - Ввод: "омуль" -> Вывод: Рыба
    - Ввод: "эдельвейс" -> Вывод: Трава
    - Ввод: "можжевельник" -> Вывод: Кустарник
    - Ввод: "остров Ольхон" -> Вывод: Другое
    """
        return ChatPromptTemplate.from_messages([
            ("system", category_system_prompt),
            ("human", "{object_name}")
        ])

    def _get_intent_detection_prompt(self) -> ChatPromptTemplate:
        """Создает промпт для определения намерения."""
        intent_system_prompt = """
Твоя задача — определить одно из следующих намерений пользователя по его запросу.

Возможные намерения:
- get_picture: пользователь хочет увидеть изображение.
- get_location: пользователь хочет увидеть ареал обитания на общей карте.
- get_intersection_object_on_map: пользователь ищет объект рядом с конкретным местом.
- get_text: пользователь хочет получить текстовое описание/факт.
- get_objects_in_polygon: пользователь хочет узнать, что растет или обитает в определенной локации.
- unknown: намерение неясно или не соответствует ни одному из вышеперечисленных.

Проанализируй запрос и ответь ТОЛЬКО ОДНИМ СЛОВОМ — названием намерения из списка выше. Не используй JSON, кавычки или какие-либо пояснения.

Примеры:
- Ввод: "покажи фото нерпы зимой" -> Вывод: get_picture
- Ввод: "где растет копеечник" -> Вывод: get_location
- Ввод: "расскажи про иву" -> Вывод: get_text
- Ввод: "какие животные есть на Ольхоне" -> Вывод: get_objects_in_polygon
- Ввод: "где найти кедр рядом с Култуком" -> Вывод: get_intersection_object_on_map
"""
        return ChatPromptTemplate.from_messages([
            ("system", intent_system_prompt),
            ("human", "{query}")
        ])

    async def detect_intent(self, query: str) -> str:
        """
        Определяет намерение пользователя, возвращая строку (например, "get_picture").
        """
        logger.info(f"Определение намерения для запроса: '{query}'")
        try:
            prompt = self._get_intent_detection_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query})
            
            intent = response.content.strip().lower()
            
            allowed_intents = ["get_picture", "get_location", "get_intersection_object_on_map", "get_text", "get_objects_in_polygon"]
            if intent in allowed_intents:
                logger.info(f"Намерение определено как: '{intent}'")
                return intent
            else:
                logger.warning(f"GigaChat вернул невалидное намерение: '{intent}'. Установлено 'unknown'.")
                return "unknown"
        except Exception as e:
            logger.error(f"Ошибка при определении намерения: {e}", exc_info=True)
            return "unknown"

    def _get_entity_extraction_prompt(self, intent: str) -> ChatPromptTemplate:
        """Создает детальный промпт для извлечения сущностей на основе известного намерения."""
        # --- БАЗОВАЯ ЧАСТЬ ПРОМПТА ---
        base_prompt = """## РОЛЕВАЯ МОДЕЛЬ:
Ты — высокоточный и внимательный парсер-аналитик запросов о флоре и фауне Байкала.
Твоя задача — проанализировать запрос пользователя с уже известным намерением и извлечь из него СТРОГО ОПРЕДЕЛЕННЫЙ набор сущностей.
Результат необходимо вернуть в формате JSON без каких-либо пояснений.

## ОПИСАНИЕ ПОЛЕЙ JSON:
- `object`: Название биологического объекта (животное, растение). Приводи его к именительному падежу. Например, "нерпой" -> "нерпа". Если объект не упомянут, верни `null`.
- `geo_place`: Название географического места (город, остров, мыс, река). Если не упомянуто, верни `null`.
- `features`: JSON-объект с дополнительными признаками объекта. Если признаков нет, верни пустой объект `{{}}`. Возможные ключи и значения:
    - `season`: "Зима", "Весна", "Лето", "Осень".
    - `habitat`: "Лес", "Поле", "Горы", "Побережье", "На дереве", "В воде", "На болоте", "На лугу".
    - `flowering`: `true`, если в запросе есть слова "цветение", "цветущий", "в цвету" и т.п.
    - `fruits_present`: "Шишка", "Ягода", "Плод", "Орех", "Желудь", если в запросе упоминаются плоды.
- `unsupported_features`: Список строк с признаками, которые ты не можешь классифицировать по словарю `features`. Это редкие, небиологические описания. Например: "на фоне заката", "рядом с машиной", "в смешной шапке". Если таких нет, верни пустой список `[]`.
- `can_fulfill`: Булево значение. Установи `false` ТОЛЬКО ЕСЛИ список `unsupported_features` НЕ ПУСТОЙ. Во всех остальных случаях — `true`.

## ИНСТРУКЦИИ ПО ИЗВЛЕЧЕНИЮ (ВАЖНО!)
В зависимости от намерения (`intent`), тебе нужно сфокусироваться на извлечении только определенных полей. Остальные поля должны иметь значения по умолчанию (`null`, `{{}}`, `[]`).
"""

        # --- ДИНАМИЧЕСКАЯ ЧАСТЬ ПРОМПТА В ЗАВИСИМОСТИ ОТ НАМЕРЕНИЯ ---
        intent_specific_instructions = ""
        if intent == "get_picture":
            intent_specific_instructions = """- **Задача для `get_picture`:** Извлеки `object` и любые `features`. Поля `geo_place` и `unsupported_features` вторичны.
- **Пример:** "Покажи фото цветущей черёмухи весной в лесу" -> извлекаются `object`, `features.flowering`, `features.season`, `features.habitat`."""
        
        elif intent == "get_location":
            intent_specific_instructions = """- **Задача для `get_location`:** Извлеки только `object`.
- **Пример:** "Где растёт багульник?" -> извлекается только `object`."""
            
        elif intent == "get_intersection_object_on_map":
            intent_specific_instructions = """- **Задача для `get_intersection_object_on_map`:** Извлеки `object` и `geo_place`. Это самые важные поля.
- **Пример:** "Где найти сибирский кедр рядом с посёлком Култук?" -> извлекаются `object` и `geo_place`."""
            
        elif intent == "get_objects_in_polygon":
            intent_specific_instructions = """- **Задача для `get_objects_in_polygon`:** Извлеки только `geo_place`.
- **Пример:** "Какая флора растет на острове Ольхон?" -> извлекается только `geo_place`."""
            
        elif intent == "get_text":
            intent_specific_instructions = """- **Задача для `get_text`:** Извлеки только `object`.
- **Пример:** "Расскажи мне о байкальской нерпе" -> извлекается только `object`."""

        # --- ПРИМЕРЫ ДЛЯ ОБУЧЕНИЯ МОДЕЛИ ---
        examples = """
## ПРИМЕРЫ:

**Запрос:** "Как выглядит эдельвейс?"
**Намерение:** `get_picture`
**Результат:**```json
{{
  "object": "эдельвейс",
  "geo_place": null,
  "features": {{}},
  "unsupported_features": [],
  "can_fulfill": true
}}

Запрос: "Покажи мне фото белки с шишкой зимой в лесу"
Намерение: get_picture
Результат:
{{
  "object": "белка",
  "geo_place": null,
  "features": {{
    "fruits_present": "Шишка",
    "season": "Зима",
    "habitat": "Лес"
  }},
  "unsupported_features": [],
  "can_fulfill": true
}}

Запрос: "Где можно встретить Ольхонскую полевку рядом с Култуком"
Намерение: get_intersection_object_on_map
Результат:
{{
  "object": "Ольхонская полевка",
  "geo_place": "Култук",
  "features": {{}},
  "unsupported_features": [],
  "can_fulfill": true
}}

Запрос: "Какую флору я могу встретить на Малом море"
Намерение: get_objects_in_polygon
Результат: 
{{
  "object": null,
  "geo_place": "Малое море",
  "features": {{}},
  "unsupported_features": [],
  "can_fulfill": true
}}

Запрос: "Покажи чайку на фоне колеса обозрения"
Намерение: get_picture
Результат:  
{{
  "object": "чайка",
  "geo_place": null,
  "features": {{}},
  "unsupported_features": ["на фоне колеса обозрения"],
  "can_fulfill": false
}}
"""
        # Собираем финальный промпт
        final_prompt = f"{base_prompt}\n{intent_specific_instructions}\n{examples}"

        return ChatPromptTemplate.from_messages([
                ("system", final_prompt),
                ("human", "Проанализируй следующий запрос с уже известным намерением.\nНамерение: `{intent}`\nЗапрос: `{query}`")
            ])

    async def extract_entities(self, query: str, intent: str) -> Dict[str, Any]:
        """Анализирует текст запроса для известного намерения и возвращает JSON с сущностями."""
        logger.info(f"Извлечение сущностей для намерения '{intent}' из запроса: '{query}'")
        try:
            prompt = self._get_entity_extraction_prompt(intent)
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query, "intent": intent})

            generated_text = response.content.strip()
            
            # Надежное извлечение JSON из ответа, даже если он обернут в текст или markdown
            start_index = generated_text.find('{')
            end_index = generated_text.rfind('}')
            if start_index != -1 and end_index != -1:
                json_text = generated_text[start_index:end_index+1]
            else:
                raise json.JSONDecodeError("JSON объект не найден в ответе LLM", generated_text, 0)
            
            parsed_json = json.loads(json_text)
            
            # Валидация и установка значений по умолчанию для полей
            parsed_json.setdefault("object", None)
            parsed_json.setdefault("geo_place", None)
            parsed_json.setdefault("features", {})
            parsed_json.setdefault("unsupported_features", [])
            parsed_json.setdefault("can_fulfill", len(parsed_json.get("unsupported_features", [])) == 0)

            logger.info(f"Сущности извлечены успешно: {json.dumps(parsed_json, ensure_ascii=False)}")
            return {"success": True, "result": parsed_json}
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Невалидный JSON: {str(e)}", "raw_text": generated_text}
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при извлечении сущностей: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Внутренняя ошибка при анализе запроса: {str(e)}"}
        
    async def get_object_category(self, object_name: str) -> Optional[str]:
        """
        Определяет категорию объекта (например, "Дерево", "Млекопитающее").
        Возвращает None в случае ошибки или если категория 'Другое'.
        """
        logger.info(f"Определение категории для объекта: '{object_name}'")
        try:
            prompt = self._get_category_detection_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"object_name": object_name})
            category = response.content.strip()

            # Проверяем, что категория из нашего списка и не "Другое"
            allowed_categories = ["Дерево", "Кустарник", "Трава", "Млекопитающее", "Птица", "Рыба", "Насекомое"]
            if category in allowed_categories:
                logger.info(f"Категория для '{object_name}' определена как: '{category}'")
                return category
            else:
                logger.info(f"Объект '{object_name}' не относится к известным категориям (получено: '{category}')")
                return None
        except Exception as e:
            logger.error(f"Ошибка при определении категории для '{object_name}': {e}")
            return None


  
  
  
  