import logging
import aiohttp
from fastapi import FastAPI, Body, HTTPException
from aiogram import types
from contextlib import asynccontextmanager

# Импорты логики бота
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager
from handlers.gigachat_handler import GigaChatHandler
from utils.settings_manager import get_user_settings, update_user_settings

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

    logger.info(f"Запрос от {user_id}: {query}")

    # 1. Настройка окружения пользователя
    full_settings = {
        "mode": request_settings.get("mode", "gigachat"),
        "debug_mode": request_settings.get("debug_mode", False),
        "gigachat_fallback": request_settings.get("gigachat_fallback", True),
        "stoplist_enabled": request_settings.get("stoplist_enabled", True)
    }
    update_user_settings(user_id, full_settings)

    # 2. Инициализация компонентов бота
    qa = QueryAnalyzer()
    cm = RedisContextManager()
    dm = DialogueManager(cm)
    
    mock_message = MockMessage(text=query, user_id=user_id)
    handler = GigaChatHandler(qa, dm, session)

    # 3. Запуск логики (асинхронно)
    if full_settings["mode"] == "gigachat":
        callback_prefixes = ["clarify_idx", "explore", "fallback", "clarify_more"]
        if ":" in query and any(p in query for p in callback_prefixes):
            cb_query = SimpleMock(
                id=f"cb_{user_id}", from_user=mock_message.from_user,
                message=mock_message, data=query
            )
            await handler.process_callback(cb_query)
        else:
            await handler.process_message(mock_message)
    
    return mock_message.bot.responses

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)