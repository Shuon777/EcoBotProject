import os
import logging
import aiohttp
from pathlib import Path
from dotenv import dotenv_values, set_key
from fastapi import FastAPI, Body, HTTPException
from aiogram import types
from typing import List
from contextlib import asynccontextmanager

# Импорты логики бота
from logic.llm_analyzer.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager
from handlers.gigachat_handler import GigaChatHandler
from utils.settings_manager import get_user_settings, update_user_settings
from logic.DialogSystem.orchestrator import DialogueSystem
from logic.DialogSystem.schemas import UserRequest, SystemResponse

# Настройки
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)



# --- ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ---
session: aiohttp.ClientSession = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global session
    session = aiohttp.ClientSession()
    logger.info("🚀 Core API: aiohttp session opened (Lifespan).")
    
    yield 
    
    await session.close()
    logger.info("💤 Core API: aiohttp session closed (Lifespan).")

app = FastAPI(title="EcoBot Core API", lifespan=lifespan)

# --- МОКИ (БЕЗ ИЗМЕНЕНИЙ) ---

class SimpleMock:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
    async def answer(self, *args, **kwargs): pass
    async def edit_text(self, *args, **kwargs): pass
    async def delete(self, *args, **kwargs): pass

class MockBot:
    def __init__(self):
        self.responses = []
    async def send_chat_action(self, *args, **kwargs): pass

class MockMessage:
    def __init__(self, text, user_id):
        self.text = text
        self.from_user = SimpleMock(id=user_id)
        self.chat = SimpleMock(id=user_id)
        self.bot = MockBot()
        self.message_id = 1

    async def _store(self, content, msg_type, **kwargs):
        status_icons = ["🔍", "📸", "🗺️", "🌿", "⌛", "🌸", "🌰", "❄️", "🌲"]
        if msg_type == "text" and any(icon in str(content) for icon in status_icons):
            return

        item = {"type": msg_type, "content": str(content)}
        if "reply_markup" in kwargs and kwargs["reply_markup"]:
            markup = kwargs["reply_markup"]
            if hasattr(markup, 'to_python'):
                item["buttons"] = markup.to_python().get("inline_keyboard", [])
        # Для карт пробрасываем специфичные поля
        if "custom_type" in kwargs and kwargs["custom_type"] == "map":
            # Эти данные обычно приходят из логики через CoreResponse
            pass 
        self.bot.responses.append(item)

    async def answer(self, text, **kwargs):
        m_type = kwargs.get("custom_type", "text")
        await self._store(text, m_type, **kwargs)
        return self

    async def answer_photo(self, photo, **kwargs):
        # Если это карта, логика может передавать доп. поля в kwargs
        item = {"type": "image", "content": str(photo)}
        if "caption" in kwargs: item["content_text"] = kwargs["caption"]
        await self._store(photo, "image", **kwargs)
        return self

    async def delete(self): return True
    async def edit_text(self, text, **kwargs):
        await self.answer(text, **kwargs)
        return self

# --- ЭНДПОИНТЫ ---

@app.post("/test_query")
async def test_query(data: dict = Body(...)):
    query = data.get("query")
    user_id = data.get("user_id", "test_user")
    request_settings = data.get("settings", {})

    # Принудительно приводим к bool, на случай если фронтенд шлет строку "true"
    debug_mode = str(request_settings.get("debug_mode", "")).lower() == "true"
    
    # Собираем чистый объект настроек
    full_settings = {
        "mode": request_settings.get("mode", "gigachat"),
        "debug_mode": debug_mode,
        "gigachat_fallback": request_settings.get("gigachat_fallback", True),
        "stoplist_enabled": request_settings.get("stoplist_enabled", True)
    }

    cm = RedisContextManager()
    ds = DialogueSystem(provider="qwen", session=session, context_manager=cm)
    
    # Передаем настройки в запрос
    request = UserRequest(
        user_id=user_id,
        query=query,
        context=[], 
        settings=full_settings
    )

    responses = await ds.process_request(request)
    
    output = []
    for r in responses:
        item = {
            "type": r.response_type,
            "buttons": r.buttons,
            "debug_info": r.debug_info
        }
        
        # ЛОГИКА ДЛЯ АДМИНКИ (chat.html):
        if r.response_type == "image":
            item["content"] = r.media_url # Ссылка для <img>
        else:
            item["content"] = r.text # Текст для пузыря
            if r.media_url:
                item["static_map"] = r.media_url # Поле для карт
        
        output.append(item)
    return output

@app.post("/clear_context")
async def clear_context(data: dict = Body(...)):
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="No user_id")
    
    cm = RedisContextManager()
    await cm.delete_context(user_id)
    if cm.redis_client:
        await cm.redis_client.delete(f"clarify_options:{user_id}")
        await cm.redis_client.delete(f"fallback_attributes:{user_id}")
    
    return {"status": "cleared"}

BASE_DIR = Path(__file__).parent
PROMPTS_DIR = BASE_DIR / "logic" / "llm_analyzer"
ENV_PATH = BASE_DIR / ".env"

PROMPT_FILES =[
    "classifications_actions_part_of_prompt.txt",
    "classifications_entities_part_of_prompt.txt",
    "examples_entity.txt",
    "examples_for_prompt.txt"
]

@app.get("/prompts")
async def get_prompts():
    """Отдает текущее содержимое всех файлов с промптами"""
    prompts = {}
    for filename in PROMPT_FILES:
        filepath = PROMPTS_DIR / filename
        if filepath.exists():
            prompts[filename] = filepath.read_text(encoding="utf-8")
        else:
            prompts[filename] = ""
    return prompts

@app.post("/prompts")
async def update_prompts(data: dict = Body(...)):
    """Перезаписывает файлы с промптами новыми данными"""
    for filename, content in data.items():
        if filename in PROMPT_FILES:
            filepath = PROMPTS_DIR / filename
            filepath.write_text(content, encoding="utf-8")
    return {"status": "success", "message": "Промпты успешно обновлены"}

@app.get("/config")
async def get_config():
    """Отдает текущие переменные из .env"""
    if ENV_PATH.exists():
        return dotenv_values(ENV_PATH)
    return {}

@app.post("/config")
async def update_config(data: dict = Body(...)):
    """Точечно обновляет значения в файле .env"""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
        
    for key, value in data.items():
        # set_key из python-dotenv безопасно обновляет ключи, сохраняя комментарии
        set_key(str(ENV_PATH), key, str(value))
    return {"status": "success", "message": "Конфигурация обновлена"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)