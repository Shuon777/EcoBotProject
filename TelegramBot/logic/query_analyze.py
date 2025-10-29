import os
import json
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate

from logic.prompts import UniversalPrompts, StandardPrompts 

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
            return {
                "statistics": f"В локации {geo_place} найдено {len(objects_list)} биологических объектов.",
                "interesting_objects": [{"name": obj, "reason": "интересный объект"} for obj in objects_list[:3]]
            }