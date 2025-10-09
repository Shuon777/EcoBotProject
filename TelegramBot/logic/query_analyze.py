# --- НАЧАЛО ФАЙЛА: logic/query_analyze.py ---

import os
import json
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate

# Импортируем промпты из отдельного файла
from logic.prompts import GeoPrompts, StandardPrompts

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

    async def _detect_geo_intent(self, query: str) -> str:
        """Определяет тип географического запроса"""
        try:
            prompt = GeoPrompts.geo_check_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query})
            
            geo_type = response.content.strip().lower()
            valid_types = ["geo_list", "geo_info", "geo_count", "not_geo"]
            
            if geo_type in valid_types:
                logger.info(f"Тип географического запроса: {geo_type}")
                return geo_type
            else:
                logger.warning(f"Неизвестный тип географического запроса: {geo_type}")
                return "not_geo"
                
        except Exception as e:
            logger.error(f"Ошибка при определении типа географического запроса: {e}")
            return "not_geo"

    async def _detect_standard_intent(self, query: str) -> str:
        """Определяет намерение для не-географических запросов"""
        try:
            prompt = StandardPrompts.intent_detection_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query})
            
            intent = response.content.strip().lower()
            
            allowed_intents = ["get_picture", "get_location", "get_intersection_object_on_map", 
                              "get_text", "get_objects_in_polygon", "unknown"]
            if intent in allowed_intents:
                logger.info(f"Стандартное намерение определено как: '{intent}'")
                return intent
            else:
                logger.warning(f"GigaChat вернул невалидное намерение: '{intent}'. Установлено 'unknown'.")
                return "unknown"
        except Exception as e:
            logger.error(f"Ошибка при определении стандартного намерения: {e}", exc_info=True)
            return "unknown"

    async def detect_intent(self, query: str) -> str:
        """
        Определяет намерение пользователя, возвращая строку.
        """
        logger.info(f"Определение намерения для запроса: '{query}'")
        
        try:
            # ШАГ 1: Сначала проверяем тип географического запроса
            geo_type = await self._detect_geo_intent(query)
            
            if geo_type != "not_geo":
                # Маппим типы географических запросов на намерения
                geo_intent_map = {
                    "geo_list": "get_geo_objects",
                    "geo_info": "get_geo_info", 
                    "geo_count": "get_geo_count"
                }
                intent = geo_intent_map.get(geo_type, "get_geo_objects")
                logger.info(f"Запрос '{query}' -> географическое намерение: {intent}")
                return intent
            
            # ШАГ 2: Если не географический - используем обычную логику
            return await self._detect_standard_intent(query)
            
        except Exception as e:
            logger.error(f"Ошибка при определении намерения: {e}", exc_info=True)
            return "unknown"

    async def _extract_geo_entities(self, query: str) -> Dict[str, Any]:
        """Извлекает сущности ТОЛЬКО для географических запросов по НОВОЙ структуре"""
        logger.info(f"Извлечение ГЕОГРАФИЧЕСКИХ сущностей из запроса: '{query}'")
        
        try:
            prompt = GeoPrompts.geo_entity_extraction_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query})

            generated_text = response.content.strip()
            
            # Извлечение JSON
            start_index = generated_text.find('{')
            end_index = generated_text.rfind('}')
            if start_index != -1 and end_index != -1:
                json_text = generated_text[start_index:end_index+1]
            else:
                raise json.JSONDecodeError("JSON объект не найден в ответе LLM", generated_text, 0)
            
            parsed_json = json.loads(json_text)
            
            # НОРМАЛИЗАЦИЯ и ВАЛИДАЦИЯ новой структуры
            if "location_info" not in parsed_json:
                parsed_json = {"location_info": {}, "geo_type": {}}
                
            # Гарантируем наличие всех полей
            location_info = parsed_json.setdefault("location_info", {})
            location_info.setdefault("exact_location", None)
            location_info.setdefault("region", None)
            location_info.setdefault("nearby_places", [])
            
            geo_type = parsed_json.setdefault("geo_type", {})
            geo_type.setdefault("primary_type", [])
            geo_type.setdefault("specific_types", [])

            logger.info(f"Географические сущности извлечены: {json.dumps(parsed_json, ensure_ascii=False)}")
            return {"success": True, "result": parsed_json}
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON для географических сущностей: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Невалидный JSON: {str(e)}", "raw_text": generated_text}
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при извлечении географических сущностей: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Внутренняя ошибка: {str(e)}"}

    async def _extract_standard_entities(self, query: str, intent: str) -> Dict[str, Any]:
        """Существующая логика извлечения сущностей (без изменений)"""
        try:
            prompt = StandardPrompts.entity_extraction_prompt(intent)
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

            logger.info(f"Стандартные сущности извлечены успешно: {json.dumps(parsed_json, ensure_ascii=False)}")
            return {"success": True, "result": parsed_json}
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Невалидный JSON: {str(e)}", "raw_text": generated_text}
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при извлечении стандартных сущностей: {str(e)}", exc_info=True)
            return {"success": False, "error": f"Внутренняя ошибка при анализе запроса: {str(e)}"}

    async def extract_entities(self, query: str, intent: str) -> Dict[str, Any]:
        """Главный метод извлечения сущностей с РАЗДЕЛЕНИЕМ логики"""
        logger.info(f"Извлечение сущностей для намерения '{intent}' из запроса: '{query}'")
        
        # Определяем тип запроса по намерению
        geo_intents = ["get_geo_objects", "get_geo_info", "get_geo_count"]
        
        if intent in geo_intents:
            # НОВАЯ логика для географических запросов
            return await self._extract_geo_entities(query)
        else:
            # СТАРАЯ логика для всех остальных запросов
            return await self._extract_standard_entities(query, intent)
        
    async def get_object_category(self, object_name: str) -> Optional[str]:
        """
        Определяет категорию объекта (например, "Дерево", "Млекопитающее").
        Возвращает None в случае ошибки или если категория 'Другое'.
        """
        logger.info(f"Определение категории для объекта: '{object_name}'")
        try:
            prompt = StandardPrompts.category_detection_prompt()
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
        
    async def analyze_location_objects(self, geo_place: str, objects_list: list) -> dict:
        """
        Анализирует список объектов локации через GigaChat
        """
        try:
            prompt = f"""
            Проанализируй эти биологические объекты из локации "{geo_place}":
            {', '.join(objects_list)}
            
            Верни ответ в JSON формате:
            {{
                "statistics": "краткая статистика 2-3 предложения",
                "interesting_objects": [
                    {{
                        "name": "название объекта1",
                        "reason": "короткое объяснение почему интересен (1 предложение)"
                    }}
                ]
            }}
            
            Выбери 3 самых уникальных/интересных объекта и кратко объясни их значимость.
            """
            
            logger.info(f"Отправляем запрос к LLM для {geo_place}")
            response = await self.llm.ainvoke(prompt)
            logger.info(f"Получен ответ от LLM: {response.content}")
            
            # Пробуем извлечь JSON
            json_text = response.content.strip()
            start_index = json_text.find('{')
            end_index = json_text.rfind('}')
            
            if start_index != -1 and end_index != -1:
                json_text = json_text[start_index:end_index+1]
                result = json.loads(json_text)
                logger.info(f"JSON успешно распарсен: {result}")
                return result
            else:
                logger.error(f"JSON не найден в ответе: {response.content}")
                raise ValueError("JSON не найден в ответе LLM")
                
        except Exception as e:
            logger.error(f"Ошибка анализа локации через LLM: {e}", exc_info=True)
            # Fallback - простая статистика
            return {
                "statistics": f"В локации {geo_place} найдено {len(objects_list)} биологических объектов.",
                "interesting_objects": [{"name": obj, "reason": "интересный объект"} for obj in objects_list[:3]]
            }