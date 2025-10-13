import os
import json
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate

# [ИЗМЕНЕНО] Импортируем только новый универсальный промпт
from logic.prompts import UniversalPrompts, StandardPrompts 

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

    # [НОВОЕ] Единственный публичный метод для анализа запроса
    async def analyze_query(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Анализирует запрос пользователя с помощью универсального промпта и возвращает структурированный JSON.
        """
        logger.info(f"Универсальный анализ запроса: '{query}'")
        try:
            prompt = UniversalPrompts.analysis_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"query": query})

            generated_text = response.content.strip()
            
            # Надежное извлечение JSON из ответа, даже если он обернут в текст или markdown
            start_index = generated_text.find('{')
            end_index = generated_text.rfind('}')
            if start_index != -1 and end_index != -1:
                json_text = generated_text[start_index:end_index+1]
            else:
                logger.error(f"JSON не найден в ответе LLM для запроса '{query}'. Ответ: {generated_text}")
                return None
            
            parsed_json = json.loads(json_text)
            logger.info(f"Запрос '{query}' успешно проанализирован: {json.dumps(parsed_json, ensure_ascii=False)}")
            return parsed_json
        
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON для запроса '{query}': {str(e)}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при анализе запроса '{query}': {str(e)}", exc_info=True)
            return None

    # [СОХРАНЕНО] Этот метод может понадобиться для логики диалога, пока оставляем
    async def get_object_category(self, object_name: str) -> Optional[str]:
        """
        Определяет категорию объекта (например, "Дерево", "Млекопитающее").
        Возвращает None в случае ошибки или если категория 'Другое'.
        """
        logger.info(f"Определение категории для объекта: '{object_name}'")
        try:
            # Используем старый промпт, так как он хорошо справляется с этой узкой задачей
            prompt = StandardPrompts.category_detection_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({"object_name": object_name})
            category = response.content.strip()

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