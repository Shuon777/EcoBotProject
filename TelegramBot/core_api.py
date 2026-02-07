# Файл: TelegramBot/test_api.py

import asyncio
import logging
import aiohttp
from flask import Flask, request, jsonify
from aiogram import types

# Импорты вашей логики
from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager
from handlers.gigachat_handler import GigaChatHandler
from handlers.rasa_handler import RasaHandler
from utils.settings_manager import get_user_settings, update_user_settings
from utils.bot_utils import normalize_message

# --- НАСТРОЙКА ---
TEST_API_PORT = 5001
# -----------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)
app = Flask(__name__)

class SimpleMock:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
    
    # Добавляем обязательные асинхронные методы, которые вызывает бот
    async def answer(self, *args, **kwargs): pass
    async def edit_text(self, *args, **kwargs): pass
    async def delete(self, *args, **kwargs): pass

# --- УЛУЧШЕННЫЕ ЗАГЛУШКИ (Mocks) ---
class MockBot:
    def __init__(self):
        self.responses = []
    async def send_chat_action(self, *args, **kwargs):
        pass # Игнорируем статусы "печатает..."

class MockChat:
    def __init__(self, chat_id):
        self.id = chat_id

class MockMessage:
    def __init__(self, text, user_id):
        self.text = text
        # Эмулируем структуру пользователя aiogram
        self.from_user = types.User(id=int(user_id) if user_id.isdigit() else 12345, is_bot=False, first_name="Test")
        self.chat = MockChat(user_id)
        self.bot = MockBot()
        self.message_id = 1

    async def _store(self, data, **kwargs):
        # Список иконок, которые бот использует ТОЛЬКО для статусов
        status_icons = ["🔍", "📸", "🗺️", "🌿", "⌛", "🌸", "🌰", "❄️", "🌲"]
        content = data.get("content", "") or ""

        # Если в тексте есть хоть одна статусная иконка — это статус
        if data["type"] == "text" and any(icon in content for icon in status_icons):
            return

        # Для API переименуем "photo" в "image" для единообразия
        if data["type"] == "photo":
            data["type"] = "image"

        for key, value in kwargs.items():
            if hasattr(value, 'to_python'):
                data[key] = value.to_python()
            else:
                data[key] = value
        self.bot.responses.append(data)

    async def answer(self, text, **kwargs):
        await self._store({"type": "text", "content": text}, **kwargs)
        return self 

    async def answer_photo(self, photo, **kwargs):
        await self._store({"type": "photo", "content": str(photo)}, **kwargs)
        return self

    # --- НОВЫЕ МЕТОДЫ-ЗАГЛУШКИ ДЛЯ FEEDBACK MANAGER ---

    async def delete(self):
        """Заглушка метода удаления. В API нам не нужно реально удалять."""
        return True

    async def edit_text(self, text, **kwargs):
        """Метод для редактирования текста (нужен для пагинации)."""
        await self._store({"type": "text", "content": text}, **kwargs)
        return self

    async def edit_reply_markup(self, reply_markup=None):
        """Заглушка для редактирования кнопок."""
        return True
# --- API ---

@app.route('/test_query', methods=['POST'])
def test_query_route():
    data = request.get_json()
    if not data: return jsonify({"error": "Request body must be JSON"}), 400
    
    query = data.get("query")
    user_id = str(data.get("user_id", "test_user_123"))
    request_settings = data.get("settings", {})

    logger.info(f"Test Query [{user_id}]: '{query}'")

    original_settings = get_user_settings(user_id)
    try:
        # Временно подменяем настройки
        temp_settings = {"mode": request_settings.get("mode", "gigachat")}
        # Если нужно, можно добавить другие настройки в temp_settings
        update_user_settings(user_id, temp_settings)
        
        result = asyncio.run(run_bot_logic(query, user_id, temp_settings["mode"]))
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error testing query: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
    finally:
        update_user_settings(user_id, original_settings)

async def run_bot_logic(query: str, user_id: str, mode: str):
    async with aiohttp.ClientSession() as session:
        # Инициализация компонентов
        qa = QueryAnalyzer()
        context_manager = RedisContextManager()
        dialogue_manager = DialogueManager(context_manager)
        
        # --- ИСПРАВЛЕНИЕ ТУТ ---
        # Мы используем user_id напрямую, не пытаясь превратить его в int,
        # чтобы сохранить консистентность со строковым ID из админки.
        mock_user = SimpleMock(id=user_id) 
        
        mock_chat = SimpleMock(id=user_id)
        mock_bot = MockBot() 
        
        mock_message = MockMessage(text=query, user_id=user_id)
        mock_message.bot = mock_bot
        mock_message.chat = mock_chat
        mock_message.from_user = mock_user

        gigachat_h = GigaChatHandler(qa, dialogue_manager, session)

        if mode == "gigachat":
            # Проверяем, является ли запрос командой колбэка
            # Список префиксов должен быть полным
            callback_prefixes = ["clarify_idx", "explore", "fallback", "clarify_more"]
            is_callback = ":" in query and any(p in query for p in callback_prefixes)
            
            if is_callback:
                logger.info(f"CORE_API: Обработка CALLBACK: {query} для пользователя {user_id}")
                callback_query = SimpleMock(
                    id="web_cb_" + str(user_id),
                    from_user=mock_user, # Здесь будет .id == "admin_web_interface"
                    message=mock_message,
                    data=query
                )
                await gigachat_h.process_callback(callback_query)
            else:
                logger.info(f"CORE_API: Обработка MESSAGE: {query} для пользователя {user_id}")
                await gigachat_h.process_message(mock_message)
        
        return mock_message.bot.responses
        
if __name__ == '__main__':
    print(f"🚀 Тестовый сервер запущен: http://0.0.0.0:{TEST_API_PORT}/test_query")
    print(f"Пример запроса: curl -X POST http://localhost:{TEST_API_PORT}/test_query -H 'Content-Type: application/json' -d '{{\"query\": \"Где обитает нерпа?\"}}'")
    app.run(host='0.0.0.0', port=TEST_API_PORT, debug=False)