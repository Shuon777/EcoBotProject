# Файл: TelegramBot/test_api.py

import asyncio
import logging
import aiohttp
from flask import Flask, request, jsonify
from aiogram import types

from logic.query_analyze import QueryAnalyzer
from logic.dialogue_manager import DialogueManager
from utils.context_manager import RedisContextManager
from handlers.gigachat_handler import GigaChatHandler
from handlers.rasa_handler import RasaHandler
from utils.settings_manager import get_user_settings, update_user_settings
from utils.bot_utils import normalize_message

# --- НАСТРОЙКА ---
# Тестовый API будет работать на порту 5001
TEST_API_PORT = 5001
# -----------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)
app = Flask(__name__)

# --- Классы-заглушки для имитации aiogram ---
class MockBot:
    def __init__(self):
        self.responses = []
    async def send_chat_action(self, *args, **kwargs):
        pass

class MockChat:
    def __init__(self, chat_id):
        self.id = chat_id

class MockMessage:
    def __init__(self, text, user_id):
        self.text = text
        self.from_user = {"id": user_id}
        self.chat = MockChat(user_id)
        self.bot = MockBot()

    async def _process_and_store_response(self, base_response: dict, **kwargs):
        processed_kwargs = {}
        for key, value in kwargs.items():
            if isinstance(value, (types.InlineKeyboardMarkup, types.ReplyKeyboardMarkup)):
                processed_kwargs[key] = value.to_python()
            else:
                processed_kwargs[key] = value
        
        base_response.update(processed_kwargs)
        self.bot.responses.append(base_response)

    async def answer(self, text, **kwargs):
        await self._process_and_store_response({"type": "text", "content": text}, **kwargs)

    async def answer_photo(self, photo, **kwargs):
        await self._process_and_store_response({"type": "photo", "content": photo}, **kwargs)

# --- Эндпоинт для тестирования ---
@app.route('/test_query', methods=['POST'])
def test_query_route():
    data = request.get_json()
    if not data: return jsonify({"error": "Request body must be JSON"}), 400
    query = data.get("query")
    if not query: return jsonify({"error": "Field 'query' is required"}), 400

    user_id = data.get("user_id", "test_user_123")
    request_settings = data.get("settings", {})

    logger.info(f"Получен запрос для user_id='{user_id}': '{query}'")

    original_settings = get_user_settings(user_id)
    try:
        temp_settings = {"mode": request_settings.get("mode", "gigachat")}
        update_user_settings(user_id, temp_settings)
        
        result = asyncio.run(run_bot_logic(query, user_id, temp_settings["mode"]))
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Произошла непредвиденная ошибка: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    finally:
        update_user_settings(user_id, original_settings)

async def run_bot_logic(query: str, user_id: str, mode: str):
    async with aiohttp.ClientSession() as session:
        qa = QueryAnalyzer()
        context_manager = RedisContextManager()
        dialogue_manager = DialogueManager(context_manager)
        gigachat_h = GigaChatHandler(qa, dialogue_manager, session)
        rasa_h = RasaHandler(session)
        
        mock_message = MockMessage(text=query, user_id=user_id)
        
        if mode == "gigachat":
            await gigachat_h.process_message(mock_message)
        else:
            rasa_responses_raw = await rasa_h.process_message(mock_message)
            if rasa_responses_raw:
                for r in (rasa_responses_raw or []):
                    mock_message.bot.responses.append(normalize_message(r))

        return mock_message.bot.responses

if __name__ == '__main__':
    logger.info(f"Запуск Тестового API на http://0.0.0.0:{TEST_API_PORT}/test_query")
    app.run(host='0.0.0.0', port=TEST_API_PORT, debug=False)