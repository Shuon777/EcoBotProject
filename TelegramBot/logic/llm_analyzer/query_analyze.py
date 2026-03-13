import os
import json
import logging
from typing import Dict, Any, Optional
from langchain_gigachat import GigaChat
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import ValidationError

from .prompts import UniversalPrompts
from .validator import AnalysisResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class QueryAnalyzer:
    def __init__(self):
        """Инициализация обработчика запроса пользователя"""
        self.prompts_cache = {}  # Кэш текстов промптов
        self.prompts_mtime = {}  # Кэш времени последнего изменения файлов
        try:
            self.llm = self._init_ollama()
            
            # Добавим отладку структуры директорий
            current_dir = os.path.dirname(os.path.abspath(__file__))
            llm_dir = os.path.join(current_dir)
            logger.info(f"📁 Директория llm_analyzer: {llm_dir}")
            
            if os.path.exists(llm_dir):
                files = os.listdir(llm_dir)
                logger.info(f"📋 Файлы в llm_analyzer: {files}")
            else:
                logger.error(f"❌ Директория llm_analyzer не существует: {llm_dir}")
            logger.info("GigaChat успешно инициализирован.")
        except Exception as e:
            logger.error(f"Ошибка инициализации GigaChat: {str(e)}")
            raise
    def _get_prompt_part(self, file_path: str) -> str:
        """Берет промпт из кэша. Читает с диска только если файл обновился."""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(current_dir, file_path)
        
        try:
            # Получаем время последнего изменения файла (работает мгновенно)
            current_mtime = os.path.getmtime(full_path)
            
            # Если файл есть в кэше и не менялся — отдаем из памяти
            if file_path in self.prompts_cache and self.prompts_mtime.get(file_path) == current_mtime:
                return self.prompts_cache[file_path]
                
            # Иначе читаем с диска и обновляем кэш
            with open(full_path, 'r', encoding='utf-8') as file:
                content = file.read().strip()
                self.prompts_cache[file_path] = content
                self.prompts_mtime[file_path] = current_mtime
                logger.info(f"✅ Промпт загружен/обновлен в кэше: {file_path}")
                return content
                
        except FileNotFoundError:
            logger.error(f"❌ Файл {full_path} не найден. Использую пустую строку.")
            return ""
        except Exception as e:
            logger.error(f"❌ Ошибка при чтении {full_path}: {e}")
            return ""
        
    def _init_ollama(self):
        """Подключение к локальной модели через туннель"""
        logger.info("🤖 Инициализация LOCAL OLLAMA (Qwen)")
        return ChatOpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama", 
            model="qwen2.5:7b", 
            temperature=0.1
        )

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
                scope="GIGACHAT_API_PERS"
            )
        except Exception as e:
            logger.error(f"Ошибка создания GigaChat инстанса: {str(e)}")
            raise

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


    def _extract_json_safe(self, text: str) -> Optional[str]:
        if not text: return None
        text = text.strip()
        # Qwen часто пишет: "Here is the JSON: ```json ... ```"
        # Находим первую { и последнюю }
        start, end = text.find('{'), text.rfind('}')
        if start == -1 or end == -1: return None
        
        json_str = text[start:end+1]
        
        # Исправление сдвоенных скобок (бывает у Qwen)
        if json_str.startswith("{{") and json_str.endswith("}}"): 
            json_str = json_str[1:-1]
            
        return json_str

    async def _make_llm_request(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Делает запрос к LLM с валидацией и автоматическим исправлением ошибок (Retry Loop).
        """
        MAX_RETRIES = 2  # Сколько раз даем шанс исправиться
        
        current_query_prompt = query
        # Базовый промпт
        prompt_template = UniversalPrompts.analysis_prompt()
        chain = prompt_template | self.llm

        current_actions = self._get_prompt_part('prompts_structure/classifications_actions_part_of_prompt.txt')
        current_examples = self._get_prompt_part("prompts_structure/examples_for_prompt.txt")
        current_types = self._get_prompt_part('prompts_structure/classifications_entities_part_of_prompt.txt')
        #current_flora = self._get_prompt_part('prompts_structure/examples_entity.txt')

        for attempt in range(MAX_RETRIES + 1):
            try:
                # Логируем попытку
                if attempt > 0:
                    logger.info(f"🔄 Попытка исправления #{attempt} для запроса '{query}'")

                response = await chain.ainvoke({
                    "query": current_query_prompt,  
                    "actions": current_actions, 
                    "examples": current_examples, 
                    "types": current_types,
                })
                
                generated_text = response.content.strip()
                json_text = self._extract_json_safe(generated_text)
                
                if not json_text:
                    raise ValueError("JSON не найден в ответе LLM")

                parsed_json = json.loads(json_text)
                
                # --- ВАЛИДАЦИЯ PYDANTIC ---
                # Это выбросит ошибку ValidationError, если структура неверна
                validated_model = AnalysisResponse(**parsed_json)
                
                # Если всё ок, превращаем обратно в dict (exclude_none=False важно, чтобы null поля остались null)
                result_dict = validated_model.model_dump(by_alias=True)\
                
                if not result_dict.get("search_query"):
                    result_dict["search_query"] = query
                
                logger.info(f"✅ Успешная валидация (Попытка {attempt}). Action: {result_dict.get('action')}")
                return result_dict

            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                error_msg = str(e)
                logger.warning(f"⚠️ Ошибка валидации на попытке {attempt}: {error_msg}")
                
                # Если это была последняя попытка - сдаемся
                if attempt == MAX_RETRIES:
                    logger.error(f"❌ Не удалось получить валидный ответ после {MAX_RETRIES} попыток.")
                    return None
                
                # Если есть попытки - формируем "исправляющий" промпт для следующей итерации
                # Мы добавляем сообщение об ошибке к тексту запроса, эмулируя диалог
                current_query_prompt = (
                    f"{query}\n\n"
                    f"SYSTEM ERROR: Твой предыдущий ответ содержал ошибку валидации:\n{error_msg}\n\n"
                    f"ЗАДАЧА: Исправь JSON и верни его ПОЛНОСТЬЮ.\n"
                    f"1. Не забудь поле `search_query`.\n"
                    f"2. Убедись, что 'type' это Biological, GeoPlace или Infrastructure (не Unknown).\n"
                    f"3. Верни валидный JSON."
                )
            
            except Exception as e:
                logger.error(f"Критическая ошибка LLM: {e}", exc_info=True)
                return None
        return None
    
    async def answer_general_question(self, query: str) -> str:
        """
        Генерирует ответ на общие вопросы о боте (Кто ты? Что умеешь?),
        следуя заданной персоне и инструкции.
        """
        system_prompt = (
            "Ты — Эко-ассистент по поиску объектов флоры и фауны (ОФФ) в Прибайкалье.\n"
            "Твоя база знаний основана на данных Байкальского музея СО РАН.\n\n"
            "Твоя задача — объяснить пользователю, кто ты и как тобой пользоваться.\n"
            "Следуй этой структуре ответа:\n"
            "1. Представься, упомянув, что ты работаешь на данных Байкальского музея СО РАН.\n"
            "2. Скажи: «Я могу отвечать на вопросы следующего типа:» и приведи список шаблонов:\n"
            "   — Расскажи о [название вида] (получить описание)\n"
            "   — Как выглядит [название] (поиск фото)\n"
            "   — Где растет/обитает [название] (карта ареала)\n"
            "   — Что обитает рядом с [Локация] (поиск по месту)\n"
            "   — Список [категория] в [Локация] (например, музеи в Иркутске)\n\n"
            "Отвечай дружелюбно, но по делу. Не придумывай функции, которых нет в списке."
        )

        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=query)
            ]
            
            logger.info(f"Генерация ответа 'О боте' для запроса: {query}")
            response = await self.llm.ainvoke(messages)
            return response.content.strip()

        except Exception as e:
            logger.error(f"Ошибка при генерации ответа о боте: {e}", exc_info=True)
            # Фоллбэк, если генерация сломалась
            return (
                "Я бот по поиску флоры и фауны Прибайкалья на основе данных Байкальского музея СО РАН.\n"
                "Я умею искать описания, фото и карты ареалов обитания, а также подсказывать, "
                "какие животные и растения есть в конкретных местах."
            )
    
    async def reply_to_small_talk(self, query: str) -> str:
        """
        Генерирует ответ на приветствия, благодарности и оффтоп.
        """
        system_prompt = (
            "Ты — дружелюбный Эко-ассистент по флоре и фауне Байкала. Твоя база знаний — Байкальский музей СО РАН.\n"
            "Твоя задача — поддержать короткий разговор (Small Talk).\n\n"
            "Правила:\n"
            "1. Если это приветствие -> Поздоровайся и расскажи кто ты.\n"
            "2. Если это благодарность -> Скажи «Пожалуйста» или «Рад помочь».\n"
            "3. Если вопрос «Как дела?» -> Ответь позитивно, упомяни, что готов работать.\n"
            "4. Если тема НЕ касается природы, Байкала или твоих функций (оффтоп) -> Вежливо скажи, что ты разбираешься только в флоре и фауне Байкала.\n"
            "5. Отвечай кратко (1-2 предложения)."
            "6. Твой ответ на сообщение НЕ должен содержать вопрос."
        )

        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=query)
            ]
            
            # Используем немного выше температуру (по умолчанию), чтобы ответы были живее
            response = await self.llm.ainvoke(messages)
            return response.content.strip()

        except Exception as e:
            logger.error(f"Ошибка при генерации Small Talk: {e}", exc_info=True)
            return "Здравствуйте! Я готов помочь вам узнать больше о природе Байкала."

    async def analyze_query(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Анализирует запрос пользователя с помощью универсального промпта и возвращает структурированный JSON.
        Внимание: LLM работает БЕЗ истории. Обогащением контекста занимается DialogueManager.
        """
        logger.info(f"Универсальный анализ запроса (State-less): '{query}'")
        
        # Делаем единственный прямой запрос без истории
        result = await self._make_llm_request(query)
        
        if result is not None:
            logger.info(f"✅ Анализ успешен: '{query}' -> Action: {result.get('action')}")
        else:
            logger.error(f"❌ LLM не смогла распарсить запрос: '{query}'")
            
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