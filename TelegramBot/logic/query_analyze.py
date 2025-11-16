import os
import json
import logging
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate

from .prompts_structure.prompts import UniversalPrompts

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()

def load_prompt_part(file_path):
    """
    Функция для загрузки файла, содержайщий часть универсального промпта
    """
    try:
        # Получаем директорию, где находится query_analyze.py
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Формируем полный путь к файлу
        full_path = os.path.join(current_dir, file_path)
        
        logger.info(f"🔄 Пытаемся загрузить: {full_path}")
        
        with open(full_path, 'r', encoding='utf-8') as file:
            content = file.read().strip()
            logger.info(f"✅ Файл загружен: {file_path} ({len(content)} символов)")
            return content
    except FileNotFoundError:
        logger.error(f"❌ Файл {full_path} не найден. Использую пустую строку.")
        return ""
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении {full_path}: {e}")
        return ""

class QueryAnalyzer:
    def __init__(self):
        """Инициализация обработчика запроса пользователя"""
        try:
            self.llm = self._init_gigachat()
            
            # Добавим отладку структуры директорий
            current_dir = os.path.dirname(os.path.abspath(__file__))
            prompts_dir = os.path.join(current_dir, 'prompts_structure')
            logger.info(f"📁 Текущая директория: {current_dir}")
            logger.info(f"📁 Директория prompts_structure: {prompts_dir}")
            
            if os.path.exists(prompts_dir):
                files = os.listdir(prompts_dir)
                logger.info(f"📋 Файлы в prompts_structure: {files}")
            else:
                logger.error(f"❌ Директория prompts_structure не существует: {prompts_dir}")
            
            self.examples = load_prompt_part("prompts_structure/examples_for_prompt.txt")
            self.actions = load_prompt_part('prompts_structure/classifications_actions_part_of_prompt.txt')
            self.types = load_prompt_part('prompts_structure/classifications_entities_part_of_prompt.txt')
            self.flora = load_prompt_part('prompts_structure/examples_entity.txt')
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

    def _create_history_block(self, history: Optional[Dict[str, Any]]) -> str:
        """Создает блок истории из данных с фильтрацией ответов API"""
        if not history:
            return ""
            
        prev_query = history.get("query")
        prev_response_list = history.get("response", [])
        
        # Извлекаем и фильтруем текстовое содержимое
        prev_response_texts = []
        for resp in prev_response_list:
            if resp.get("type") == "text":
                content = str(resp.get("content", ""))
                # Фильтруем нежелательные ответы
                if not self._is_blocked_response(content):
                    prev_response_texts.append(content)
        
        # Если текста не было (например, только карта), возьмем caption
        if not prev_response_texts:
            for resp in prev_response_list:
                if resp.get("caption"):
                    caption = str(resp.get("caption", ""))
                    if not self._is_blocked_response(caption):
                        prev_response_texts.append(caption)

        prev_response = "\n".join(prev_response_texts).strip()

        if prev_query and prev_response:
            history_block = (
                "---\n"
                "Предыдущий диалог:\n"
                f"- Запрос пользователя: \"{prev_query}\"\n"
                f"- Ответ бота: {prev_response}\n"
                "---\n"
            )
            logger.info(f"Используется контекст: {history_block}")
            return history_block
        return ""

    def _is_blocked_response(self, text: str) -> bool:
        """Проверяет, является ли ответ заблокированной фразой"""
        blocked_phrases = [
            "я не готов разговаривать",
            "я не могу разговаривать", 
            "я не умею разговаривать",
            "Я не готов про это разговаривать"
        ]
        text_lower = text.lower()
        return any(phrase in text_lower for phrase in blocked_phrases)

    async def _make_llm_request(self, query: str, history_block: str) -> Optional[Dict[str, Any]]:
        """Делает запрос к LLM и парсит результат"""
        try:
            prompt = UniversalPrompts.analysis_prompt()
            chain = prompt | self.llm
            response = await chain.ainvoke({
                "query": query, 
                "history_block": history_block, 
                "actions": self.actions, 
                "examples": self.examples, 
                "types": self.types,
                "flora": self.flora
            })
            
            generated_text = response.content.strip()
            
            start_index = generated_text.find('{')
            end_index = generated_text.rfind('}')
            if start_index != -1 and end_index != -1:
                json_text = generated_text[start_index:end_index+1]
                parsed_json = json.loads(json_text)
                logger.info(f"Запрос '{query}' успешно проанализирован: {json.dumps(parsed_json, ensure_ascii=False)}")
                return parsed_json
            else:
                logger.warning(f"JSON не найден в ответе LLM для запроса '{query}'. Ответ: {generated_text}")
                return None
                
        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга JSON для запроса '{query}': {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Непредвиденная ошибка при анализе запроса '{query}': {str(e)}")
            return None

    async def analyze_query(self, query: str, history: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Анализирует запрос пользователя с помощью универсального промпта и возвращает структурированный JSON.
        """
        logger.info(f"Универсальный анализ запроса: '{query}'")
    
        # Первая попытка - с историей
        history_block = self._create_history_block(history)
        result = await self._make_llm_request(query, history_block)
        
        # Если первая попытка не удалась и была история, пробуем без истории
        if result is None and history:
            logger.info(f"🔄 Повторный запрос без истории для: '{query}'")
            result = await self._make_llm_request(query, "")
            
            if result is not None:
                logger.info(f"✅ Повторный запрос без истории успешен для: '{query}'")
            else:
                logger.error(f"❌ Оба запроса (с историей и без) не удались для: '{query}'")
        
        return result
        
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